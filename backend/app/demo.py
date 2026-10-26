"""Replay project for the live demo: the planned 3-shot cut, its ops, the voice-note transcript and a
colour-coded matte track, served from DATA_DIR/demo so the editor works with no model calls at all.

Build once (dev): python -m backend.app.demo --dev ../dev --stock ../stock
"""
from __future__ import annotations
import argparse, json, shutil, subprocess
from pathlib import Path

import cv2
import numpy as np

from . import config, store
from .ops import Op, Spotlight
from .pipeline import CutShot, _masks

COLOURS = {"blur": (180, 77, 255), "title_behind": (0, 176, 255), "spotlight": (61, 255, 198)}  # BGR


def matte_track(shots: list[CutShot], ops: list[Op], out: Path, fps: float = 24000 / 1001, size=(640, 360)) -> Path:
    """One video, cut-length: black, with each op's SAM target painted in its colour while the op is live.
    The editor lays it over the player with mix-blend-mode: screen."""
    frames, cut_t = [], 0.0
    for shot in shots:
        cap = cv2.VideoCapture(str(shot.src)); sfps = cap.get(cv2.CAP_PROP_FPS)
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)); cap.release()
        a, b = int(round(shot.t_in * sfps)), int(round(shot.t_out * sfps))
        dur = (b - a) / sfps
        live = [o for o in ops if o.t1 > cut_t and o.t0 < cut_t + dur and o.body.kind != "ask"]
        tracks = []
        for o in live:
            ids = [str(o.body.target.object_id)] if o.body.target.object_id is not None else None
            fg = _masks(shot.sam[o.body.target.concept], a, b - a, w, h, ids, primary=isinstance(o.body, Spotlight))
            tracks.append((o, fg))
        n_out = int(round(dur * fps))
        for k in range(n_out):
            t = cut_t + k / fps
            si = min(b - a - 1, int(round((t - cut_t) * sfps)))
            img = np.zeros((size[1], size[0], 3), np.uint8)
            for o, fg in tracks:
                if not (o.t0 <= t <= o.t1):
                    continue
                m = cv2.resize(fg[si], size, interpolation=cv2.INTER_LINEAR).astype(bool)
                img[m] = COLOURS[o.body.kind]
                edge = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
                img[edge] = (255, 255, 255)
            frames.append(img)
        cut_t += dur
    p = subprocess.Popen(["timeout", "-k", "5", "600", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{size[0]}x{size[1]}", "-r", f"{fps}", "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-threads", "4", str(out)],
        stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close(); p.wait()
    if p.returncode != 0:
        raise RuntimeError("matte track encode failed")
    return out


def build(dev: Path, stock: Path, after_name: str = "cut_after_v3_planned.mp4") -> dict:
    d = config.DATA_DIR / "demo"; d.mkdir(parents=True, exist_ok=True)
    shots = [
        CutShot(stock / "mixkit_4837_720.mp4", 0.0, 4.0, {"phone": dev / "sam31_4837.json"}),
        CutShot(stock / "mixkit_101_720.mp4", 0.5, 8.5, {"man in sunglasses": dev / "sam31_101_man_in_sunglasses.json", "man": dev / "sam31_101.json"}),
        CutShot(stock / "mixkit_134_720.mp4", 9.0, 15.0, {"man": dev / "sam31_134.json"}),
    ]
    plan = json.load(open(dev / "planner_ops.json"))
    ops = [Op(**o) for o in plan["ops"]]
    shutil.copy(dev / "cut_before.mp4", d / "before.mp4")
    shutil.copy(dev / after_name, d / "after.mp4")
    matte_track(shots, ops, d / "mattes.mp4")
    vn = next((p for p in [stock / "client_voice_note.mp3"] if p.exists()), None)
    if vn:
        shutil.copy(vn, d / f"voice_note{vn.suffix}")
    for n in ("before", "after", "mattes"):           # VP9 twins for browsers without H.264
        subprocess.run(["timeout", "-k", "5", "600", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(d / f"{n}.mp4"), "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "38", "-deadline", "realtime", "-cpu-used", "8",
            "-row-mt", "1", "-threads", "4", "-an", str(d / f"{n}.webm")], check=True)
    tr = json.load(open(dev / "voice_note_transcript.json")) if (dev / "voice_note_transcript.json").exists() else None
    doc = {
        "id": "demo", "name": "Client cut · revision 5", "replay": True,
        "media": {"before_url": "/media/demo/before.mp4", "after_url": "/media/demo/after.mp4",
                  "mattes_url": "/media/demo/mattes.mp4", "proxy_url": "/media/demo/before.mp4",
                  "voice_url": f"/media/demo/voice_note{vn.suffix}" if vn else None,
                  "duration": 18.018, "fps": 24000 / 1001, "width": 1280, "height": 720},
        "shots": [{"src": str(s.src), "t_in": s.t_in, "t_out": s.t_out, "sam": {k: str(v) for k, v in s.sam.items()}} for s in shots],
        "notes": plan.get("notes", []),
        "transcript": tr,
        "planner": {"model": plan.get("model"), "seconds": plan.get("seconds")},
        "ops": [o.model_dump(mode="json") for o in ops],
    }
    store.upsert(doc)
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--dev", type=Path, required=True); ap.add_argument("--stock", type=Path, required=True)
    ap.add_argument("--after", default="cut_after_v3_planned.mp4")
    a = ap.parse_args()
    doc = build(a.dev.resolve(), a.stock.resolve(), a.after)
    print(json.dumps({k: doc[k] for k in ("id", "name", "media")}, indent=1))
