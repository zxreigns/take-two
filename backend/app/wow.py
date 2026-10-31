"""Showcase replay project ("wow"): three client notes, three edits a pro would need hours of roto for.

  1. remove   the man walking through the plaza, and his shadow (SAM 3.1 masks -> VOID video inpainting)
  2. sky      a flat blue sky to golden hour (SAM 3.1 "sky" matte, horizon-locked plate, edge decontamination)
  3. recolour her red dress to royal blue (SAM 3.1 "red dress" matte, Lab hue rotation; roses untouched)

The renders, the raw SAM 3.1 answers and the client's voice note come from dev/wow (see its NOTES.md).
This module only assembles them into a project the editor can open with no model calls: per-shot
before/after segments, the joined cut, a colour-coded matte track, timeline thumbnails, the transcript.

    python -m app.wow --wow ../../dev/wow
"""
from __future__ import annotations
import argparse, json, shutil, subprocess
from pathlib import Path

import cv2
import numpy as np

from . import config, store
from .sam_fal import decode_rle

FPS = 25
W, H = 1280, 720
# BGR, matching the editor's KIND colours
COLOURS = {"remove": (250, 139, 167), "sky": (36, 191, 251), "recolour": (182, 114, 244)}   # Signal: #A78BFA, #FBBF24, #F472B6

SHOTS = [
    # dir, op kind, SAM json(s), first SAM frame used, SAM fps, client words, target concept, op body extras
    dict(dir="removal_v2", kind="remove", sam=["walk_full_sam_person.json", "walk_full_sam_shadow.json"], ids=[[1], [0]], f0=26, sfps=30000 / 1001,
         concept="man walking", note="On the plaza shot, there's a guy walking right through the middle. Can you get rid of him?",
         say_t=(3.74, 9.92), extra={"also": "his shadow", "engine": "VOID video inpainting"},
         rationale="Remove the walking man and his cast shadow; rebuild the plaza behind him on every frame."),
    dict(dir="sky", kind="sky", sam=["sky_full_sam.json"], f0=0, sfps=25.0,
         concept="sky", note="Then the one on the path, the sky looks a bit dead. Make it golden hour.",
         say_t=(10.68, 15.56), extra={"look": "golden hour"},
         rationale="Replace the flat sky with a sunset plate locked to the horizon; warm the land to match."),
    dict(dir="recolour", kind="recolour", sam=["dress_full_sam.json"], f0=0, sfps=25.0,
         concept="red dress", note="And the last shot, her dress needs to be royal blue. That's the brand colour.",
         say_t=(16.2, 21.4), extra={"colour": "royal blue", "hex": "#2B4BD8"},
         rationale="Shift only the dress to royal blue, keeping its folds and shading; skin and roses stay as shot."),
]


def ff(*args: str) -> None:
    subprocess.run(["timeout", "-k", "5", "600", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def norm(src: Path, out: Path) -> None:
    ff("-i", str(src), "-vf", f"fps={FPS},scale={W}:{H},format=yuv420p", "-an", "-c:v", "libx264", "-crf", "18",
       "-preset", "medium", "-threads", "2", "-movflags", "+faststart", str(out))


def frames_of(p: Path) -> int:
    cap = cv2.VideoCapture(str(p)); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)); cap.release(); return n


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".txt"); lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)); lst.unlink()


def webm(src: Path) -> None:
    ff("-i", str(src), "-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-deadline", "realtime", "-cpu-used", "8",
       "-threads", "2", "-an", str(src.with_suffix(".webm")))


def sam_masks(paths: list[Path], width: int, height: int, ids: list[list[int]] | None = None) -> list[np.ndarray]:
    """Union of the chosen objects (all, when ids is None) in every SAM answer, per frame, as full-frame bool masks.
    The walk's "person" answer also holds tiny distant people; only the walker (id 1) and his shadow (id 0) were removed."""
    per: list[np.ndarray] | None = None
    for i, p in enumerate(paths):
        keep = set(ids[i]) if ids else None
        r = json.loads(p.read_text())
        objs = r.get("objects") or []
        cur = []
        for f, recs in enumerate(objs):
            m = np.zeros((height, width), np.uint8)
            for rec in recs or []:
                if keep is None or rec["object_id"] in keep:
                    m |= decode_rle(rec["rle"], width, height)
            cur.append(m.astype(bool))
        per = cur if per is None else [a | b for a, b in zip(per, cur)]
    return per or []


def matte_frames(shot: dict, wow: Path, n_out: int, size=(640, 360)) -> list[np.ndarray]:
    masks = sam_masks([wow / "masks" / s for s in shot["sam"]], W, H, shot.get("ids"))
    col = np.array(COLOURS[shot["kind"]], np.uint8)
    out = []
    for k in range(n_out):
        si = min(len(masks) - 1, shot["f0"] + int(round(k * shot["sfps"] / FPS)))
        m = cv2.resize(masks[si].astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
        img = np.zeros((size[1], size[0], 3), np.uint8)
        img[m] = (col * 0.75).astype(np.uint8)
        edge = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
        img[edge] = (255, 255, 255)
        out.append(img)
    return out


def encode_raw(frames: list[np.ndarray], out: Path, size=(640, 360)) -> None:
    p = subprocess.Popen(["timeout", "-k", "5", "600", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "2", str(out)],
        stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close(); p.wait()
    if p.returncode:
        raise RuntimeError("matte encode failed")


def thumbs(src: Path, d: Path, prefix: str, n: int, dur: float) -> list[str]:
    d.mkdir(exist_ok=True)
    names = []
    for i in range(n):
        t = (i + 0.5) * dur / n
        name = f"{prefix}_{i:02d}.jpg"
        ff("-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=192:108", "-q:v", "4", str(d / name))
        names.append(name)
    return names


def build(wow: Path, voice: Path, transcript: Path, name: str = "Plaza spot · revision 2", client: str = "Ada") -> dict:
    d = config.DATA_DIR / "wow"
    shutil.rmtree(d, ignore_errors=True); (d / "seg").mkdir(parents=True)
    befores, afters, mattes, shots_doc, ops, notes = [], [], [], [], [], []
    t = 0.0
    for i, s in enumerate(SHOTS, 1):
        b, a = d / "seg" / f"shot{i}_before.mp4", d / "seg" / f"shot{i}_after.mp4"
        norm(wow / s["dir"] / "before.mp4", b); norm(wow / s["dir"] / "after.mp4", a)
        n = min(frames_of(b), frames_of(a)); dur = n / FPS
        befores.append(b); afters.append(a)
        mattes += matte_frames(s, wow, n)
        nid, oid = f"n{i}wow", f"op{i}wow"
        notes.append({"id": nid, "t": round(t, 3), "text": s["note"], "source": "voice", "said": list(s["say_t"])})
        body = {"kind": s["kind"], "target": {"concept": s["concept"], "object_id": None}, **s["extra"]}
        ops.append({"id": oid, "note_id": nid, "t0": round(t, 3), "t1": round(t + dur, 3), "body": body,
                    "status": "proposed", "rationale": s["rationale"], "frames": n})
        shots_doc.append({"src": str(b), "t_in": 0.0, "t_out": dur, "sam": {}, "baked": {"op": oid, "before": str(b), "after": str(a)}})
        t += dur
    concat(befores, d / "before.mp4"); concat(afters, d / "after.mp4")
    encode_raw(mattes, d / "mattes.mp4")
    for n in ("before", "after", "mattes"):
        webm(d / f"{n}.mp4")
    shutil.copy(voice, d / "voice_note.mp3")
    th = thumbs(d / "before.mp4", d / "thumbs", "b", 18, t) and thumbs(d / "after.mp4", d / "thumbs", "a", 18, t)
    lines = [{"t0": l["t0"], "t1": l["t1"], "text": l["text"]} for l in json.loads(transcript.read_text())]
    doc = {
        "id": "wow", "name": name, "client": client, "replay": True,
        "media": {"before_url": "/media/wow/before.mp4", "after_url": "/media/wow/after.mp4", "mattes_url": "/media/wow/mattes.mp4",
                  "proxy_url": "/media/wow/before.mp4", "voice_url": "/media/wow/voice_note.mp3", "duration": round(t, 3),
                  "fps": FPS, "width": W, "height": H,
                  "thumbs": {"before": [f"/media/wow/thumbs/b_{i:02d}.jpg" for i in range(18)],
                             "after": [f"/media/wow/thumbs/a_{i:02d}.jpg" for i in range(18)]}},
        "notes": notes, "ops": ops, "shots": shots_doc, "transcript": lines,
        "planner": {"masks": "SAM 3.1", "baked": True}, "live": None,
    }
    store.upsert(doc)
    return doc


# The showcase's run, as it was recorded (compute seconds; fal queue waits are not shown). The editor replays it
# through the same live-progress view a fresh upload streams into, sped up and labelled as a replay.
#   transcribe  faster-whisper small on this box            planner run 2026-10-31 (dev/wow/planner_ops_wow.json)
#   plan        Muse Spark Open on Fireworks                 same run
#   masks       SAM 3.1 on fal, 4 prompts in parallel        dev/wow/NOTES.md (17-20 s per prompt; dress 12 s)
#   render      VOID pass 1 + pass 2 (60 s + 172 s), then the local composites (sky ~60 s, recolour 10 s)
RUN_JOBS = [  # shot, SAM prompt, op kind, SAM json, object ids, done at (s after masks start), objects kept
    (1, "person", "remove", "walk_full_sam_person.json", [1], 20.0, 1),
    (1, "shadow", "remove", "walk_full_sam_shadow.json", [0], 17.0, 1),
    (2, "sky", "sky", "sky_full_sam.json", None, 18.0, 1),
    (3, "red dress", "recolour", "dress_full_sam.json", None, 12.0, 1),
]
JOB_COLOURS = {"person": (250, 139, 167), "shadow": (246, 92, 139), "sky": (36, 191, 251), "red dress": (182, 114, 244)}


def add_run(wow: Path, plan_json: Path, paint_n: int = 8) -> dict:
    doc = store.get("wow"); d = config.DATA_DIR / "wow"
    pj = json.loads(plan_json.read_text())
    # the edit cards now come from the real planner run: its targets, rationale and spans
    for op, p in zip(doc["ops"], pj["ops"]):
        assert op["body"]["kind"] == p["body"]["kind"], (op["body"]["kind"], p["body"]["kind"])
        op["body"]["target"]["concept"] = p["body"]["target"]["concept"]
        op["rationale"] = p["rationale"]
        op["t0"], op["t1"] = p["t0"], p["t1"]
    # rebuild the matte track in the Signal colours
    mattes = []
    for i, s in enumerate(SHOTS, 1):
        mattes += matte_frames(s, wow, frames_of(d / "seg" / f"shot{i}_before.mp4"))
    encode_raw(mattes, d / "mattes.mp4"); webm(d / "mattes.mp4")

    shot_t = [(0.0, doc["ops"][0]["t1"]), (doc["ops"][1]["t0"], doc["ops"][1]["t1"]), (doc["ops"][2]["t0"], doc["ops"][2]["t1"])]
    pdir = d / "paint"; shutil.rmtree(pdir, ignore_errors=True); pdir.mkdir()
    kdir = d / "keyframes"; shutil.rmtree(kdir, ignore_errors=True); kdir.mkdir()
    cap = cv2.VideoCapture(str(d / "before.mp4"))
    keyframes = []
    t = 0.5
    while t < doc["media"]["duration"]:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * FPS)); ok, f = cap.read()
        if not ok: break
        cv2.imwrite(str(kdir / f"t{t:05.1f}.jpg"), cv2.resize(f, (320, 180), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 80])
        keyframes.append({"t": t, "url": f"/media/wow/keyframes/t{t:05.1f}.jpg"}); t += 1.0
    t_tr, t_pl = pj["transcribe_seconds"], pj["seconds"]
    T = {"ingest": (0.0, 0.9)}
    T["transcribe"] = (0.9, 0.9 + t_tr); T["plan"] = (T["transcribe"][1], T["transcribe"][1] + t_pl)
    m0 = T["plan"][1]; T["masks"] = (m0, m0 + max(j[5] for j in RUN_JOBS)); T["render"] = (T["masks"][1], T["masks"][1] + 232.0 + 41.0)
    jobs, paint = [], []
    for k, (shot, concept, kind, js, ids, done_at, nobj) in enumerate(RUN_JOBS):
        jid = f"j{k + 1}"
        jobs.append({"id": jid, "shot": shot, "concept": concept, "kind": kind, "status": "done", "painted": paint_n, "objects": nobj,
                     "t_done": round(m0 + done_at, 2)})
        s = SHOTS[shot - 1]
        masks = sam_masks([wow / "masks" / js], W, H, [ids] if ids else None)
        vb = cv2.VideoCapture(str(d / "seg" / f"shot{shot}_before.mp4")); n = int(vb.get(cv2.CAP_PROP_FRAME_COUNT))
        col = np.array(JOB_COLOURS[concept], np.float32); hexcol = "#%02X%02X%02X" % tuple(int(c) for c in col[::-1])
        for q in range(paint_n):
            fi = int((q + 0.5) * n / paint_n)
            vb.set(cv2.CAP_PROP_POS_FRAMES, fi); ok, fr = vb.read()
            si = min(len(masks) - 1, s["f0"] + int(round(fi * s["sfps"] / FPS)))
            fr = cv2.resize(fr, (640, 360), interpolation=cv2.INTER_AREA).astype(np.float32)
            m = cv2.resize(masks[si].astype(np.uint8), (640, 360), interpolation=cv2.INTER_NEAREST).astype(bool)
            out = fr * 0.62
            out[m] = fr[m] * 0.45 + col * 0.55
            edge = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
            out[edge] = (255, 255, 255)
            name = f"{jid}_{q}.jpg"
            cv2.imwrite(str(pdir / name), out.clip(0, 255).astype(np.uint8), [cv2.IMWRITE_JPEG_QUALITY, 82])
            paint.append({"job": jid, "shot": shot, "frame": fi, "t": round(shot_t[shot - 1][0] + fi / FPS, 3),
                          "url": f"/media/wow/paint/{name}", "colour": hexcol,
                          "at": round(m0 + done_at * (0.35 + 0.65 * (q + 1) / paint_n), 2)})
        vb.release()
    cap.release()
    msgs = {"ingest": "3 shots, 17.2 s", "transcribe": f"{len(pj['transcript'])} lines · Whisper (local)",
            "plan": f"3 edits · Muse Spark Open", "masks": "4 tracks · SAM 3.1", "render": "VOID + sky + recolour"}
    labels = dict(__import__("app.live", fromlist=["STAGES"]).STAGES)
    run = {"pid": "wow", "state": "done", "version": 1, "started": 0.0, "finished": T["render"][1], "replay": True,
           "stages": [{"key": k, "label": labels[k], "status": "done", "t0": T[k][0], "t1": T[k][1], "msg": msgs[k]} for k in T],
           "shots": [{"index": i + 1, "t0": a, "t1": b, "frames": int(round((b - a) * FPS)), "thumb": f"/media/wow/thumbs/b_{[2, 8, 14][i]:02d}.jpg"}
                     for i, (a, b) in enumerate(shot_t)],
           "keyframes": keyframes, "transcript": pj["transcript"], "ops": doc["ops"], "notes": doc["notes"], "jobs": jobs,
           "paint": paint, "render": 1.0, "error": None,
           "source": "Recorded compute times: Whisper and Muse Spark Open (planner run %s), SAM 3.1 and VOID (dev/wow/NOTES.md). fal queue waits not shown." % pj["ran_at"]}
    doc["run"] = run
    doc["planner"] = {"model": "Muse Spark Open", "seconds": t_pl, "masks": "SAM 3.1", "baked": True, "ran_at": pj["ran_at"]}
    store.put(doc)
    return run


def add_thumbs(pid: str, n: int = 18) -> None:
    """Timeline thumbnails for an existing project (the editor's picture lane)."""
    doc = store.get(pid); m = doc["media"]; d = config.DATA_DIR / pid
    for key, url in (("before", m.get("before_url") or m["proxy_url"]), ("after", m.get("after_url"))):
        if url:
            src = d / Path(url).name
            thumbs(src, d / "thumbs", key[0], n, m["duration"])
    m["thumbs"] = {k: [f"/media/{pid}/thumbs/{k[0]}_{i:02d}.jpg" for i in range(n)] for k in ("before", "after") if m.get(f"{k}_url")}
    store.put(doc)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wow", default="../../dev/wow")
    ap.add_argument("--thumbs-for", help="only add timeline thumbnails to this project id")
    ap.add_argument("--run", action="store_true", help="add the recorded run (replay) and planner cards to the built project")
    a = ap.parse_args()
    if a.thumbs_for:
        add_thumbs(a.thumbs_for)
    elif a.run:
        w = Path(a.wow).resolve()
        r = add_run(w, w / "planner_ops_wow.json")
        print([(s["key"], round(s["t1"] - s["t0"], 1)) for s in r["stages"]], r["finished"], len(r["paint"]))
    else:
        w = Path(a.wow).resolve()
        doc = build(w, w / "voice" / "client_voice_note_wow.mp3", w / "voice" / "transcript.json")
        print(json.dumps({k: doc[k] for k in ("id", "name")}), doc["media"]["duration"], [o["t0"] for o in doc["ops"]])
