"""SAM 3.1 on Meta Model API: request, cache, parse, and turn into per-frame mattes.

SAM answers one short noun phrase per request and streams one line per frame:
    <Nf>id<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H|><|mask;x=0;y=0;data=h,w,<enc>payload|>,id...
Masks are box-local rasters (they cover the record's box, not the whole frame).
We keep the raw stream text on disk per (project, concept): it is the cache, the
replay source for the live demo, and the audit trail of what the model returned.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import httpx
import numpy as np
from meta_sam_parser import CompletedOutcome, decode_mask_to_raster, video_segmentation_format
from meta_sam_parser import SegmentationBoxRecord, SegmentationMaskRecord

from . import config
from .meta_client import _key


@dataclass
class Track:
    """One object's geometry on one frame."""
    frame: int
    object_id: str
    box: tuple[float, float, float, float]          # half-open mask bounds: left, top, right, bottom (source px)
    raster: np.ndarray                               # box-local 0/1 uint8, h x w


def cache_path(project_dir: Path, concept: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in concept.lower()).strip("_")
    return project_dir / "sam" / f"{safe}.txt"


def _decode(args: tuple[str, str, int, int]) -> np.ndarray:
    from meta_sam_parser import SegmentationMask
    enc, payload, w, h = args
    return np.frombuffer(decode_mask_to_raster(SegmentationMask(enc, payload, w, h)), np.uint8).reshape(h, w)


def parse_py(raw: str, frames: range | None = None, workers: int = 8) -> dict[int, dict[str, Track]]:
    """Raw SAM stream text -> {frame: {object_id: Track}} using Meta's own parser.
    Mask decoding is CPU-bound pure Python, so it fans out over a process pool and
    can be limited to the frame range an op actually needs."""
    from concurrent.futures import ProcessPoolExecutor
    parser = video_segmentation_format().create_parser()
    parser.push(raw if raw.endswith("\n") else raw + "\n", emit=False)
    result = parser.finish(CompletedOutcome()).result
    boxes: dict[tuple[int, str], tuple[float, float, float, float]] = {}
    todo: list[tuple[int, str, tuple, tuple]] = []
    for rec in result.records:
        if rec.frame is None or (frames is not None and rec.frame.frame_index not in frames):
            continue
        f = rec.frame.frame_index
        if isinstance(rec, SegmentationBoxRecord):
            boxes[(f, rec.object_id)] = (rec.left, rec.top, rec.right, rec.bottom)
        elif isinstance(rec, SegmentationMaskRecord):
            b = rec.bounds
            todo.append((f, rec.object_id, (b.left, b.top, b.right, b.bottom),
                         (rec.mask.encoding, rec.mask.payload, rec.mask.width, rec.mask.height)))
    args = [t[3] for t in todo]
    if len(args) > 8 and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            rasters = list(ex.map(_decode, args, chunksize=4))
    else:
        rasters = [_decode(a) for a in args]
    out: dict[int, dict[str, Track]] = {}
    for (f, oid, bounds, _), r in zip(todo, rasters):
        out.setdefault(f, {})[oid] = Track(frame=f, object_id=oid, box=bounds, raster=r)  # mask bounds are half-open
    return out


DECODER = Path(__file__).resolve().parent.parent / "samdecode" / "decode.mjs"


def parse(raw: str, frames: range | None = None) -> dict[int, dict[str, Track]]:
    """Raw SAM stream text -> {frame: {object_id: Track}}.
    Decodes with Meta's TypeScript parser under Node (about 300x faster per core than the
    pure-Python reference decoder); falls back to Python if Node is unavailable."""
    import struct, subprocess, tempfile, shutil
    if not DECODER.exists() or not shutil.which("node"):
        return parse_py(raw, frames)
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.txt", Path(td) / "out.bin"
        src.write_text(raw)
        args = ["timeout", "-k", "5", "300", "node", str(DECODER), str(src), str(dst)]
        if frames is not None:
            args += [str(frames.start), str(frames.stop - 1)]
        subprocess.run(args, check=True, capture_output=True, cwd=DECODER.parent)
        buf = dst.read_bytes()
    out: dict[int, dict[str, Track]] = {}
    o = 0
    while o < len(buf):
        f, n = struct.unpack_from("<ii", buf, o); o += 8
        oid = buf[o:o + n].decode(); o += n
        l, t, r, b = struct.unpack_from("<ffff", buf, o); o += 16
        h, w = struct.unpack_from("<ii", buf, o); o += 8
        raster = np.frombuffer(buf, np.uint8, h * w, o).reshape(h, w); o += h * w
        out.setdefault(f, {})[oid] = Track(frame=f, object_id=oid, box=(l, t, r, b), raster=raster)
    return out


def full_mask(track: Track, width: int, height: int) -> np.ndarray:
    """Place a box-local raster into a full-frame float mask (0..1) at width x height."""
    m = np.zeros((height, width), np.float32)
    l, t, r, b = (int(round(v)) for v in track.box)
    l, t = max(l, 0), max(t, 0)
    r, b = min(max(r, l + 1), width), min(max(b, t + 1), height)
    if r <= l or b <= t:
        return m
    m[t:b, l:r] = cv2.resize(track.raster.astype(np.float32), (r - l, b - t), interpolation=cv2.INTER_LINEAR)
    return m


def object_ids(tracks: dict[int, dict[str, Track]]) -> list[str]:
    seen: dict[str, int] = {}
    for per in tracks.values():
        for oid in per:
            seen[oid] = seen.get(oid, 0) + 1
    return sorted(seen, key=lambda o: -seen[o])      # most-present first


def stream_segment_video(file_id: str, concept: str, out: Path) -> Iterator[str]:
    """POST /v1/responses with sam-3.1 on an uploaded video; yield each frame line as it
    arrives and append it to the cache file. Request shape is pinned on Day 1 against
    the live API (input_video by Files API id)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "model": "sam-3.1",
        "stream": True,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_video", "file_id": file_id},
        ]}],
        "metadata": {"mask_encoding": "one_bit"},
    }
    with out.open("w") as cache, httpx.Client(timeout=None) as h, h.stream(
        "POST", f"{config.MODEL_API_BASE}/responses", json=body,
        headers={"Authorization": f"Bearer {_key()}", "Accept": "text/event-stream"},
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            ev = json.loads(data)
            if ev.get("type") == "response.output_text.delta" and ev.get("delta"):
                cache.write(ev["delta"])
                cache.flush()
                yield ev["delta"]


# ---- synthetic SAM output, for tests and for the offline renderer -----------------
def encode_line(frame: int, objects: dict[str, np.ndarray], width: int, height: int) -> str:
    """Encode full-frame binary masks into one SAM wire line (one_bit), like the API does."""
    from meta_sam_parser._mask_codec import _encode_raster  # Meta's own encoder
    recs = []
    for oid, m in objects.items():
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            continue
        l, t, r, b = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())   # wire boxes are inclusive
        local = (m[t:b + 1, l:r + 1] > 0).astype(np.uint8)
        payload = _encode_raster(local.flatten().tolist(), local.shape[1], local.shape[0])
        recs.append(f"{oid}<|box;x1={l};y1={t};x2={r};y2={b};w={width};h={height}|>"
                    f"<|mask;x=0;y=0;data={local.shape[0]},{local.shape[1]},{payload}|>")
    return f"<{frame}f>" + ",".join(recs) if recs else ""
