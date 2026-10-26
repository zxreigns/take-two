"""Live project route: a clip + a client voice note in, a planned and rendered revision out.

Stages (each one streams its progress to the editor over SSE, see main.py):
  1. ingest      validate, 720p proxy (fps kept), shot detection, frame-exact shot segments
  2. transcribe  local faster-whisper (Muse Spark Open takes text + images, not audio)
  3. plan        Muse Spark Open turns transcript + timecoded keyframes into edit proposals
  4. masks       SAM 3.1 (fal, open weights) per shot x concept, in parallel; masks paint in as they decode
  5. render      every proposed edit rendered over the cut, plus the colour-coded matte track

Every stage caches its artifact in the project dir, so Retry resumes at the stage that failed.
"""
from __future__ import annotations
import base64
import json
import re
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import httpx
import numpy as np

from . import config, ingest, sam_fal, store
from .ops import Op, Spotlight
from .planner import Shot, plan

STAGES = [
    ("ingest", "Reading the cut"),
    ("transcribe", "Listening to the voice note"),
    ("plan", "Planning the edits"),
    ("masks", "Tracking the targets"),
    ("render", "Rendering take two"),
]
OP_COLOURS = {"blur": (180, 77, 255), "title_behind": (0, 176, 255), "spotlight": (61, 255, 198)}  # BGR, as demo.py

_runs: dict[str, dict] = {}          # pid -> live state (the SSE stream reads this)
_lock = threading.Lock()             # one live run at a time on this box
_cv = threading.Condition()


class StageError(RuntimeError):
    """An error with a sentence the editor can show as-is."""
    def __init__(self, human: str, detail: str = ""):
        super().__init__(human); self.human = human; self.detail = detail


# ---------- state ----------

def _new_state(pid: str) -> dict:
    return {"pid": pid, "state": "running", "version": 0, "started": time.time(), "finished": None,
            "stages": [{"key": k, "label": l, "status": "pending", "t0": None, "t1": None, "msg": ""} for k, l in STAGES],
            "shots": [], "keyframes": [], "transcript": None, "ops": [], "jobs": [], "paint": [], "render": 0.0,
            "error": None}


def state(pid: str) -> dict | None:
    if pid in _runs:
        return _runs[pid]
    doc = store.get(pid)
    return (doc or {}).get("live")


def _bump(st: dict, persist: bool = False) -> None:
    with _cv:
        st["version"] += 1
        _cv.notify_all()
    if persist:
        doc = store.get(st["pid"])
        if doc is not None:
            doc["live"] = {k: v for k, v in st.items()}
            store.put(doc)


def wait_change(pid: str, version: int, timeout: float = 10.0) -> dict | None:
    with _cv:
        _cv.wait_for(lambda: (state(pid) or {}).get("version", -1) != version, timeout=timeout)
    return state(pid)


def _stage(st: dict, key: str, status: str, msg: str = "") -> None:
    s = next(x for x in st["stages"] if x["key"] == key)
    if status == "running" and s["t0"] is None:
        s["t0"] = time.time()
    if status in ("done", "error", "skipped"):
        s["t1"] = time.time()
    s["status"] = status
    if msg:
        s["msg"] = msg
    _bump(st, persist=status != "running")


# ---------- helpers ----------

def _ff(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["timeout", "-k", "5", str(timeout), "nice", "-n", "10", "ffmpeg", "-hide_banner", "-y", *args],
                          capture_output=True, text=True)


def _probe_any(p: Path) -> dict:
    r = subprocess.run(["timeout", "-k", "5", "30", "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise StageError(f"Couldn't read {p.name}: it doesn't look like a media file.", r.stderr[-400:])
    return json.loads(r.stdout)


def _frame_count(p: Path) -> int:
    cap = cv2.VideoCapture(str(p)); n = 0
    while cap.grab():
        n += 1
    cap.release()
    return n


def _thumb(img: np.ndarray, width: int = 480) -> np.ndarray:
    h = int(round(img.shape[0] * width / img.shape[1] / 2) * 2)
    return cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)


# ---------- stages ----------

def _ingest(st: dict, pdir: Path, clip: Path) -> list[dict]:
    meta = pdir / "shots.json"
    if meta.exists():
        return json.loads(meta.read_text())
    j = _probe_any(clip)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise StageError("The clip has no video stream.")
    dur = float(j["format"].get("duration") or 0)
    if dur < 1.0:
        raise StageError("The clip is shorter than a second.")
    if dur > config.MAX_CLIP_S:
        raise StageError(f"The clip is {dur:.0f} s; the live route takes up to {config.MAX_CLIP_S:.0f} s. Trim it, or open the replay project.")
    _stage(st, "ingest", "running", "making a 720p proxy")
    info = ingest.probe(clip)
    fps_s = v.get("r_frame_rate", "30/1")
    num, den = (int(x) for x in fps_s.split("/"))
    if not den or num / den < 10 or num / den > 61:
        fps_s = "30/1"; num, den = 30, 1
    fps = num / den
    proxy = pdir / "proxy.mp4"
    r = _ff(["-loglevel", "error", "-i", str(clip), "-vf", f"scale=-2:{min(config.PROXY_HEIGHT, info['height'] - info['height'] % 2)},fps={fps_s},setsar=1",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", "-threads", "4", str(proxy)], timeout=600)
    if r.returncode != 0:
        raise StageError("Couldn't decode the clip.", r.stderr[-400:])
    n = _frame_count(proxy)
    cap = cv2.VideoCapture(str(proxy)); W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()

    _stage(st, "ingest", "running", "finding the cuts")
    r = _ff(["-loglevel", "info", "-i", str(proxy), "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"], timeout=300)
    cuts = sorted({int(round(float(t) * fps)) for t in re.findall(r"pts_time:([0-9.]+)", r.stderr)})
    bounds = [0]
    for c in cuts:                                   # merge flashes: a shot is at least 0.8 s
        if c - bounds[-1] >= 0.8 * fps and n - c >= 0.8 * fps:
            bounds.append(c)
    bounds.append(n)

    shots = []
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        seg = pdir / f"shot{k + 1}.mp4"
        r = _ff(["-loglevel", "error", "-i", str(proxy), "-vf", f"trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS", "-an",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", fps_s, "-threads", "4", str(seg)])
        if r.returncode != 0:
            raise StageError(f"Couldn't cut shot {k + 1} out of the clip.", r.stderr[-400:])
        cap = cv2.VideoCapture(str(seg)); cap.set(cv2.CAP_PROP_POS_FRAMES, (b - a) // 2); ok, f = cap.read(); cap.release()
        kf = pdir / f"kf_shot{k + 1}.jpg"
        if ok:
            cv2.imwrite(str(kf), _thumb(f, 480), [cv2.IMWRITE_JPEG_QUALITY, 82])
        shots.append({"index": k + 1, "src": str(seg), "frames": b - a, "first": a, "t0": round(a / fps, 4), "t1": round(b / fps, 4),
                      "thumb": f"/media/{st['pid']}/{kf.name}"})
        st["shots"] = shots; _bump(st)
    # timecoded keyframes the planner will look at (shown in the editor while it plans)
    kdir = pdir / "keyframes"; kdir.mkdir(exist_ok=True)
    cap = cv2.VideoCapture(str(proxy)); kfs = []; t = 0.5
    while t < n / fps:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps)); ok, f = cap.read()
        if not ok: break
        p = kdir / f"t{t:05.1f}.jpg"; cv2.imwrite(str(p), _thumb(f, 240), [cv2.IMWRITE_JPEG_QUALITY, 75])
        kfs.append({"t": round(t, 1), "url": f"/media/{st['pid']}/keyframes/{p.name}"}); t += 1.0
    cap.release()
    meta.write_text(json.dumps(shots))
    (pdir / "media.json").write_text(json.dumps({"fps": fps, "fps_s": fps_s, "frames": n, "width": W, "height": H,
                                                  "duration": n / fps, "keyframes": kfs}))
    return shots


_whisper = None


def _transcribe(st: dict, pdir: Path, voice: Path) -> list[dict]:
    out = pdir / "transcript.json"
    if out.exists():
        return json.loads(out.read_text())
    j = _probe_any(voice)
    if not any(s["codec_type"] == "audio" for s in j["streams"]):
        raise StageError("The voice note has no audio track.")
    dur = float(j["format"].get("duration") or 0)
    if dur > config.MAX_VOICE_S:
        raise StageError(f"The voice note is {dur:.0f} s; the live route takes up to {config.MAX_VOICE_S:.0f} s.")
    wav = pdir / "voice16k.wav"
    r = _ff(["-loglevel", "error", "-i", str(voice), "-vn", "-ac", "1", "-ar", "16000", str(wav)], timeout=120)
    if r.returncode != 0:
        raise StageError("Couldn't decode the voice note.", r.stderr[-400:])
    global _whisper
    if _whisper is None:
        _stage(st, "transcribe", "running", f"loading whisper-{config.WHISPER_MODEL}")
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=4)
    _stage(st, "transcribe", "running", f"transcribing {dur:.0f} s of audio")
    segs, _info = _whisper.transcribe(str(wav), language="en", vad_filter=True, beam_size=5,
                                      initial_prompt=config.WHISPER_PROMPT or None)
    lines = []
    for s in segs:                                   # stream lines to the editor as whisper produces them
        text = s.text.strip()
        if text:
            lines.append({"t0": round(s.start, 2), "t1": round(s.end, 2), "text": text})
            st["transcript"] = list(lines); _bump(st)
    wav.unlink(missing_ok=True)
    if not lines:
        raise StageError("The voice note came back silent: no words were heard.")
    out.write_text(json.dumps(lines, indent=1))
    return lines


def _plan(st: dict, pdir: Path, transcript: list[dict], shots: list[dict]) -> tuple[list[Op], list[dict], dict]:
    out = pdir / "plan.json"
    if out.exists():
        j = json.loads(out.read_text())
        return [Op(**o) for o in j["ops"]], j["notes"], j["planner"]
    if not config.PLANNER_API_KEY:
        raise StageError("No planner key is set (PLANNER_API_KEY), so the live route can't plan. The replay project still works.")
    _stage(st, "plan", "running", f"{config.LIVE_PLANNER_MODEL.split('/')[-1]} is reading the note and {len(shots)} shot(s)")
    t = time.time(); last = None
    for attempt in range(2):                         # one retry on a transient error
        try:
            ops, notes, raw = plan(transcript, [Shot(s["index"], s["t0"], s["t1"]) for s in shots], pdir / "proxy.mp4",
                                   base_url=config.PLANNER_BASE_URL, api_key=config.PLANNER_API_KEY, model=config.LIVE_PLANNER_MODEL, timeout=240)
            break
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code < 500 and e.response.status_code != 429:
                raise StageError(f"The planner refused the request ({e.response.status_code}).", e.response.text[:400])
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            last = e
        _stage(st, "plan", "running", "planner hiccup, trying once more")
        time.sleep(3)
    else:
        raise StageError("The planner didn't answer after two tries.", repr(last)[:400])
    dur = shots[-1]["t1"]
    cuts = [0.0] + [s["t1"] for s in shots]
    snap = lambda t: next((c for c in cuts if abs(c - t) <= 0.3), t)   # "the last shot" means from the cut, not 10 ms before it
    for o in ops:                                    # clamp to the cut, snap to shot boundaries, drop empty spans
        o.t0 = snap(max(0.0, min(o.t0, dur))); o.t1 = snap(max(o.t0, min(o.t1, dur)))
    ops = [o for o in ops if o.body.kind == "ask" or o.t1 - o.t0 >= 0.25]
    planner = {"model": config.LIVE_PLANNER_MODEL.split("/")[-1], "seconds": round(time.time() - t, 1)}
    notes_j = [n.model_dump(mode="json") for n in notes]
    out.write_text(json.dumps({"ops": [o.model_dump(mode="json") for o in ops], "notes": notes_j, "planner": planner}, indent=1))
    (pdir / "planner_raw.json").write_text(json.dumps(raw)[:2_000_000])
    return ops, notes_j, planner


def _overlaps(o: Op, s: dict) -> bool:
    return o.body.kind != "ask" and o.t1 > s["t0"] + 1e-3 and o.t0 < s["t1"] - 1e-3


def _sam_upload_uri(seg: Path, tmp: Path) -> str:
    """fal takes a base64 data URI for video_url. Same frames and size, lighter encode, to keep the request small."""
    small = tmp / (seg.stem + "_sam.mp4")
    if not small.exists():
        r = _ff(["-loglevel", "error", "-i", str(seg), "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-an", str(small)])
        if r.returncode != 0:
            raise StageError("Couldn't prepare a shot for SAM.", r.stderr[-300:])
    return "data:video/mp4;base64," + base64.b64encode(small.read_bytes()).decode()


def _sam_job(st: dict, job: dict, seg: Path, out: Path, tmp: Path) -> None:
    if out.exists():
        job["status"] = "done"; job["cached"] = True; _bump(st); return
    if not config.FAL_AUTH:
        raise StageError("No SAM key is set (FAL_KEY), so the live route can't track masks. The replay project still works.")
    H = {"Authorization": config.FAL_AUTH, "content-type": "application/json"}
    base = f"{config.FAL_BASE_URL.rstrip('/')}/{config.SAM_MODEL}"
    body = {"video_url": _sam_upload_uri(seg, tmp), "prompt": job["concept"], "return_object_rles": True, "max_num_objects": 8}
    for attempt in range(2):
        job["status"] = "submitting"; job["t0"] = time.time(); _bump(st)
        try:
            r = httpx.post(f"{base}/video-rle", headers=H, json=body, timeout=120); r.raise_for_status()
            rid = r.json()["request_id"]; job["status"] = "queued"; _bump(st)
            deadline = time.time() + 600
            while time.time() < deadline:
                time.sleep(2)
                s = httpx.get(f"{base}/requests/{rid}/status", headers=H, timeout=30).json()
                stat = {"IN_QUEUE": "queued", "IN_PROGRESS": "tracking", "COMPLETED": "decoding"}.get(s.get("status"), job["status"])
                if stat != job["status"]:
                    job["status"] = stat; _bump(st)
                if s.get("status") == "COMPLETED":
                    break
            else:
                raise TimeoutError("SAM took longer than 10 minutes")
            res = httpx.get(f"{base}/requests/{rid}", headers=H, timeout=120)
            res.raise_for_status()
            j = res.json()
            if "objects" not in j and "rle" not in j:
                raise ValueError(str(j)[:300])
            out.write_text(json.dumps(j))
            return
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 422):
                raise StageError(f"SAM refused shot {job['shot']} '{job['concept']}' ({e.response.status_code}).", e.response.text[:300])
            err = e
        except Exception as e:  # network, timeout, malformed answer: one resubmit
            err = e
        job["status"] = "retrying"; _bump(st)
    raise StageError(f"SAM didn't return masks for '{job['concept']}' in shot {job['shot']}.", repr(err)[:300])


def _paint(st: dict, job: dict, shot: dict, out: Path, pdir: Path, colour: tuple[int, int, int]) -> None:
    """Decode the masks and paint them over sampled frames, streaming each painted frame to the editor."""
    cap = cv2.VideoCapture(shot["src"]); W, Hh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tracks = sam_fal.load(out, W, Hh)
    ids = sorted({oid for f in tracks.values() for oid in f})
    job["objects"] = len(ids)
    job["frames_with_mask"] = len(tracks)
    n = shot["frames"]; step = max(1, n // 14)
    pdir_p = pdir / "paint"; pdir_p.mkdir(exist_ok=True)
    for i in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, f = cap.read()
        if not ok: break
        m = np.zeros((Hh, W), np.uint8)
        for tr in tracks.get(i, {}).values():
            l, t, r, b = tr.box; m[t:b, l:r] |= tr.raster
        img = f.astype(np.float32)
        mb = m.astype(bool)
        img[mb] = img[mb] * 0.45 + np.array(colour, np.float32) * 0.55
        edge = cv2.morphologyEx(m, cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)).astype(bool)
        img[edge] = (255, 255, 255)
        p = pdir_p / f"{job['id']}_{i:05d}.jpg"
        cv2.imwrite(str(p), _thumb(img.astype(np.uint8), 480), [cv2.IMWRITE_JPEG_QUALITY, 80])
        st["paint"].append({"job": job["id"], "shot": shot["index"], "frame": i, "t": round(shot["t0"] + i / max(1, n) * (shot["t1"] - shot["t0"]), 2),
                            "url": f"/media/{st['pid']}/paint/{p.name}", "colour": "#%02x%02x%02x" % colour[::-1]})
        job["painted"] = round(min(1.0, (i + step) / n), 3)
        _bump(st)
        time.sleep(0.06)                              # a beat per frame so the paint reads as motion
    cap.release()
    job["status"] = "done"; job["painted"] = 1.0; _bump(st)


def _masks(st: dict, pdir: Path, ops: list[Op], shots: list[dict]) -> dict[int, dict[str, str]]:
    sdir = pdir / "sam"; sdir.mkdir(exist_ok=True); tmp = pdir / "tmp"; tmp.mkdir(exist_ok=True)
    jobs = []
    for s in shots:
        for o in ops:
            if not _overlaps(o, s):
                continue
            c = o.body.target.concept
            if any(j["shot"] == s["index"] and j["concept"] == c for j in jobs):
                continue
            safe = re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") or "target"
            jobs.append({"id": f"s{s['index']}_{safe}", "shot": s["index"], "concept": c, "kind": o.body.kind,
                         "status": "pending", "painted": 0.0, "objects": None, "path": str(sdir / f"shot{s['index']}_{safe}.json")})
    st["jobs"] = jobs; st["paint"] = []; _bump(st)
    if not jobs:
        return {}
    _stage(st, "masks", "running", f"SAM 3.1 · {len(jobs)} target(s) across {len({j['shot'] for j in jobs})} shot(s), in parallel")
    paint_lock = threading.Lock()

    def run(job):
        shot = next(s for s in shots if s["index"] == job["shot"])
        _sam_job(st, job, Path(shot["src"]), Path(job["path"]), tmp)
        job["status"] = "painting"; _bump(st)
        with paint_lock:                              # paint one job at a time so the stream reads cleanly
            _paint(st, job, shot, Path(job["path"]), pdir, OP_COLOURS.get(job["kind"], (255, 255, 255)))

    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
        futs = [ex.submit(run, j) for j in jobs]
        errs = [f.exception() for f in futs]
    errs = [e for e in errs if e]
    if errs:
        raise errs[0] if isinstance(errs[0], StageError) else StageError("SAM tracking failed.", repr(errs[0])[:400])
    found = {}
    for j in jobs:
        found.setdefault(j["shot"], {})[j["concept"]] = j["path"]
    return found


def _render(st: dict, pdir: Path, ops: list[Op], shots: list[dict], sam: dict) -> dict:
    from .demo import matte_track
    from .pipeline import CutShot, render_cut
    m = json.loads((pdir / "media.json").read_text())
    cs = [CutShot(Path(s["src"]), 0.0, s["frames"] / m["fps"], {k: Path(v) for k, v in sam.get(s["index"], {}).items()}) for s in shots]
    live = [o for o in ops if o.body.kind != "ask"]
    after = pdir / "after.mp4"

    def prog(f, msg):
        st["render"] = round(0.85 * f, 3); _stage(st, "render", "running", msg)
    if not after.exists():
        try:
            render_cut(cs, live, after, pdir / "tmp", fps_out=m["fps_s"], size=f"{m['width']}:{m['height']}", progress=prog)
        except Exception as e:
            after.unlink(missing_ok=True)
            raise StageError("The render failed.", repr(e)[:400])
    st["render"] = 0.9; _stage(st, "render", "running", "matte track")
    matte_track(cs, live, pdir / "mattes.mp4", fps=m["fps"], size=(640, int(round(640 * m["height"] / m["width"] / 2) * 2)))
    before = pdir / "before.mp4"
    _ff(["-loglevel", "error", "-i", str(pdir / "proxy.mp4"), "-c", "copy", "-movflags", "+faststart", str(before)])
    if config.WEBM_TWINS:
        _stage(st, "render", "running", "browser copies")
        with ThreadPoolExecutor(3) as ex:
            list(ex.map(lambda n: _ff(["-loglevel", "error", "-i", str(pdir / f"{n}.mp4"), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36",
                                       "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-threads", "2", "-an", str(pdir / f"{n}.webm")]),
                        ["before", "after", "mattes"]))
    st["render"] = 1.0
    shutil.rmtree(pdir / "tmp", ignore_errors=True)
    return m


# ---------- orchestration ----------

def start(pid: str, clip: Path, voice: Path) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("busy")
    st = _new_state(pid); _runs[pid] = st; _bump(st, persist=True)
    threading.Thread(target=_run, args=(pid, clip, voice, st), daemon=True).start()
    return st


def _run(pid: str, clip: Path, voice: Path, st: dict) -> None:
    pdir = config.DATA_DIR / pid
    cur = "ingest"
    try:
        _stage(st, "ingest", "running")
        shots = _ingest(st, pdir, clip)
        st["shots"] = shots
        st["keyframes"] = json.loads((pdir / "media.json").read_text())["keyframes"]
        _stage(st, "ingest", "done", f"{len(shots)} shot(s), {shots[-1]['t1']:.1f} s")

        cur = "transcribe"; _stage(st, cur, "running")
        tr = _transcribe(st, pdir, voice); st["transcript"] = tr
        _stage(st, cur, "done", f"{len(tr)} line(s)")

        cur = "plan"; _stage(st, cur, "running")
        ops, notes, planner = _plan(st, pdir, tr, shots)
        st["ops"] = [o.model_dump(mode="json") for o in ops]; st["notes"] = notes
        edits = [o for o in ops if o.body.kind != "ask"]
        _stage(st, cur, "done", f"{len(edits)} edit(s) in {planner['seconds']:.0f} s" + (f", {len(ops) - len(edits)} question(s)" if len(ops) > len(edits) else ""))

        cur = "masks"; _stage(st, cur, "running")
        sam = _masks(st, pdir, ops, shots)
        _stage(st, cur, "done" if sam else "skipped", f"{sum(len(v) for v in sam.values())} track(s)" if sam else "no visual edits to track")

        cur = "render"; _stage(st, cur, "running")
        m = _render(st, pdir, ops, shots, sam)
        _stage(st, cur, "done")

        doc = store.get(pid)
        doc.update({
            "media": {"before_url": f"/media/{pid}/before.mp4", "after_url": f"/media/{pid}/after.mp4", "mattes_url": f"/media/{pid}/mattes.mp4",
                      "proxy_url": f"/media/{pid}/before.mp4", "voice_url": f"/media/{pid}/{voice.name}",
                      "duration": m["duration"], "fps": m["fps"], "width": m["width"], "height": m["height"]},
            "shots": [{"src": s["src"], "t_in": 0.0, "t_out": s["frames"] / m["fps"], "sam": {k: v for k, v in sam.get(s["index"], {}).items()}} for s in shots],
            "notes": notes, "transcript": tr, "planner": {**planner, "masks": "SAM 3.1"},
            "ops": [o.model_dump(mode="json") for o in ops],
        })
        st["state"] = "done"; st["finished"] = time.time()
        doc["live"] = st; store.put(doc); _bump(st)
    except Exception as e:
        human = e.human if isinstance(e, StageError) else f"Something broke while {dict(STAGES)[cur].lower()}."
        detail = e.detail if isinstance(e, StageError) else traceback.format_exc()[-800:]
        (pdir / "error.log").write_text(f"{cur}: {human}\n{detail}\n")
        st["state"] = "error"; st["finished"] = time.time()
        st["error"] = {"stage": cur, "message": human, "detail": detail[-400:]}
        for s in st["stages"]:
            if s["status"] == "running":
                s["status"] = "error"; s["t1"] = time.time()
        _bump(st, persist=True)
    finally:
        _lock.release()


def retry(pid: str) -> dict:
    pdir = config.DATA_DIR / pid
    inputs = json.loads((pdir / "inputs.json").read_text())
    return start(pid, pdir / inputs["clip"], pdir / inputs["voice"])
