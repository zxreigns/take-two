"""SAM 3.1 on fal (open weights): per-object RLE masks -> the same Track dicts render.py consumes.

fal's `fal-ai/sam-3-1/video-rle` with `return_object_rles=true` answers, per frame, a list of
{object_id, rle, box}. Each rle is a string of "start length" pairs over the row-major
flattened frame (index = y * W + x), at the source video's own resolution (verified Oct 26 2026:
decoded bounds match fal's normalised cxcywh box to the pixel). Object ids are stable across frames.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np

from .sam import Track


def decode_rle(s: str, width: int, height: int) -> np.ndarray:
    """'start len start len ...' (row-major) -> full-frame uint8 0/1 mask."""
    flat = np.zeros(width * height, np.uint8)
    if s:
        a = np.asarray(s.split(), dtype=np.int64)
        for st, ln in zip(a[0::2], a[1::2]):
            flat[st:st + ln] = 1
    return flat.reshape(height, width)


def _crop(m: np.ndarray, frame: int, oid: str) -> Track | None:
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    l, t, r, b = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1   # half-open bounds
    return Track(frame=frame, object_id=oid, box=(l, t, r, b), raster=m[t:b, l:r].copy())


def load(result: dict | str | Path, width: int, height: int) -> dict[int, dict[str, Track]]:
    """fal result JSON (dict or path) -> {frame: {object_id: Track}}."""
    if not isinstance(result, dict):
        result = json.loads(Path(result).read_text())
    objs = result.get("objects")
    out: dict[int, dict[str, Track]] = {}
    if isinstance(objs, list):
        for f, recs in enumerate(objs):
            for rec in recs or []:
                tr = _crop(decode_rle(rec["rle"], width, height), f, str(rec["object_id"]))
                if tr is not None:
                    out.setdefault(f, {})[tr.object_id] = tr
    else:  # merged masks only
        for f, s in enumerate(result.get("rle") or []):
            tr = _crop(decode_rle(s, width, height), f, "all")
            if tr is not None:
                out.setdefault(f, {})["all"] = tr
    return out


def merge(tracks: dict[int, dict[str, Track]], ids: list[str], width: int, height: int, as_id: str = "subject") -> dict[int, dict[str, Track]]:
    """Union several object ids into one target (e.g. a subject SAM split into two ids over time)."""
    out: dict[int, dict[str, Track]] = {}
    for f, objs in tracks.items():
        m = np.zeros((height, width), np.uint8)
        for oid in ids:
            tr = objs.get(oid)
            if tr is not None:
                l, t, r, b = tr.box
                m[t:b, l:r] |= tr.raster
        tr = _crop(m, f, as_id)
        if tr is not None:
            out[f] = {as_id: tr}
    return out
