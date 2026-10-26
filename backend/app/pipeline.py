"""Apply approved ops to a multi-shot cut: cut-time ops -> per-shot source frames -> SAM mattes -> render.

A project is a list of shots, each a source clip with an in/out point. Ops live on the cut's
timeline (that's what the planner grounds to). SAM runs per source clip and concept and is cached,
so re-planning or approving a different subset never re-segments.
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import looks, sam_fal, title_fx
from .ops import Blur, Op, Spotlight, TitleBehind


@dataclass
class CutShot:
    src: Path
    t_in: float
    t_out: float
    sam: dict[str, Path] = field(default_factory=dict)   # concept -> fal SAM 3.1 result JSON for this source clip


def _frames(src: Path, t_in: float, t_out: float) -> tuple[list[np.ndarray], float, int]:
    cap = cv2.VideoCapture(str(src)); fps = cap.get(cv2.CAP_PROP_FPS)
    a, b = int(round(t_in * fps)), int(round(t_out * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, a); fs = []
    for _ in range(b - a):
        ok, f = cap.read()
        if not ok: break
        fs.append(f)
    cap.release()
    return fs, fps, a


def _masks(json_path: Path, first: int, n: int, w: int, h: int, ids: list[str] | None = None, min_frames: int = 8,
           primary: bool = False) -> list[np.ndarray]:
    tracks = sam_fal.load(json_path, w, h)
    if ids is None:                                   # every object SAM found for the concept, minus blips
        seen: dict[str, int] = {}; area: dict[str, int] = {}
        for fi, f in tracks.items():
            if not (first <= fi < first + n): continue
            for oid, tr in f.items():
                seen[oid] = seen.get(oid, 0) + 1; area[oid] = area.get(oid, 0) + int(tr.raster.sum())
        ids = sorted(o for o, c in seen.items() if c >= min_frames)
        if primary and ids:                           # spotlight: "him" = the dominant object in this shot, not every man
            ids = [max(ids, key=lambda o: area[o])]
    merged = sam_fal.merge(tracks, ids, w, h)
    out = []
    for i in range(first, first + n):
        m = np.zeros((h, w), np.uint8); t = merged.get(i, {}).get("subject")
        if t is not None:
            l, tp, r, b = t.box; m[tp:b, l:r] = t.raster
        out.append(m)
    return out


def _encode(frames: list[np.ndarray], fps: float, out: Path) -> None:
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["timeout", "-k", "5", "900", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-threads", "4", str(out)], stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(f.tobytes())
    p.stdin.close(); p.wait()
    if p.returncode != 0: raise RuntimeError("encode failed")


def concat(parts: list[Path], out: Path, fps: str = "24000/1001", size: str = "1280:720") -> Path:
    ins, fl = [], []
    for k, p in enumerate(parts):
        ins += ["-i", str(p)]
        fl.append(f"[{k}:v]fps={fps},scale={size},setsar=1,format=yuv420p[v{k}]")
    fl.append("".join(f"[v{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v]")
    subprocess.run(["timeout", "-k", "5", "900", "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *ins,
        "-filter_complex", ";".join(fl), "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-threads", "4",
        "-movflags", "+faststart", str(out)], check=True)
    return out


def render_cut(shots: list[CutShot], ops: list[Op], out: Path, tmp: Path, ramp_s: float = 0.4) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    parts, cut_t = [], 0.0
    for si, shot in enumerate(shots):
        frames, fps, first = _frames(shot.src, shot.t_in, shot.t_out)
        h, w = frames[0].shape[:2]
        dur = len(frames) / fps
        mine = [o for o in ops if o.t1 > cut_t and o.t0 < cut_t + dur and o.body.kind != "ask"]
        layer = {"spotlight": 0, "blur": 1, "title_behind": 2}
        for op in sorted(mine, key=lambda o: layer[o.body.kind]):
            concept = op.body.target.concept
            if concept not in shot.sam:
                raise KeyError(f"no SAM result for '{concept}' on {shot.src.name}")
            ids = [str(op.body.target.object_id)] if op.body.target.object_id is not None else None
            fg = _masks(shot.sam[concept], first, len(frames), w, h, ids, primary=isinstance(op.body, Spotlight))
            lo, hi = max(op.t0, cut_t) - cut_t, min(op.t1, cut_t + dur) - cut_t      # shot-local seconds
            starts_at_cut = op.t0 <= cut_t + 1e-3
            b = op.body
            if isinstance(b, TitleBehind):
                M = title_fx.camera_track(frames, fg)
                ref = min(len(frames) - 1, int((lo + hi) / 2 * fps))
                still = None
                for i in range(len(frames)):
                    t = i / fps
                    if not (lo <= t <= hi): continue
                    tr = t - lo
                    a0 = still if (tr > 1.2 and still is not None) else title_fx.title_alpha(b.text.upper(), w, h, tr)
                    if tr > 1.2: still = a0
                    T = M[i] @ np.linalg.inv(M[ref])
                    frames[i] = title_fx.composite(frames[i], cv2.warpAffine(a0, T[:2], (w, h)), fg[i])
            else:
                anchors = looks.smooth_anchors(fg) if isinstance(b, Blur) else None
                for i in range(len(frames)):
                    t = i / fps
                    if not (lo <= t <= hi): continue
                    amt = 1.0 if starts_at_cut else looks.ease((t - lo) / ramp_s)
                    if isinstance(b, Blur):
                        frames[i] = looks.redact_mosaic(frames[i], fg[i], amount=amt, anchor=anchors[i])
                    elif isinstance(b, Spotlight):
                        frames[i] = looks.make_pop_v2(frames[i], fg[i], amount=amt * min(1.0, b.amount / 0.8))
        p = tmp / f"shot{si + 1}.mp4"; _encode(frames, fps, p); parts.append(p)
        cut_t += dur
    return concat(parts, out)
