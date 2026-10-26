"""Compositing: SAM tracks -> clean mattes -> the three ops, rendered at proxy res.

Matte hygiene (what makes SAM masks usable on a real edit):
  * hold: a frame SAM skipped (occlusion, motion blur) reuses the last mask for a few frames
  * dilate + feather: grow the edge 1-2 px and soften it so blur/title edges don't crawl
  * temporal EMA: blend each matte with the previous one to kill frame-to-frame flicker
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import sam
from .ops import Blur, Op, Spotlight, TitleBehind

FONT = Path(__file__).parent / "fonts" / "Anton-Regular.ttf"


def ease_out_cubic(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


class Matte:
    def __init__(self, tracks: dict[int, dict[str, sam.Track]], object_id: str, width: int, height: int,
                 dilate_px: int = 2, feather_px: float = 3.0, ema: float = 0.55, hold_frames: int = 4):
        self.tracks, self.oid, self.w, self.h = tracks, object_id, width, height
        self.k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1,) * 2) if dilate_px else None
        self.feather, self.ema, self.hold = feather_px, ema, hold_frames
        self.prev: np.ndarray | None = None
        self.missing = 0

    def at(self, frame: int) -> np.ndarray:
        tr = self.tracks.get(frame, {}).get(self.oid)
        if tr is None:
            self.missing += 1
            if self.prev is not None and self.missing <= self.hold:
                return self.prev
            m = np.zeros((self.h, self.w), np.float32)
        else:
            self.missing = 0
            m = sam.full_mask(tr, self.w, self.h)
            if self.k is not None:
                m = cv2.dilate(m, self.k)
            if self.feather:
                m = cv2.GaussianBlur(m, (0, 0), self.feather)
        if self.prev is not None and self.ema:
            m = self.ema * m + (1 - self.ema) * self.prev
        self.prev = m
        return m


def _title_layer(text: str, w: int, h: int, progress: float) -> tuple[np.ndarray, np.ndarray]:
    """RGBA title as (rgb float, alpha float). Rises 6% of height and fades in with progress."""
    size = int(h * 0.30)
    font = ImageFont.truetype(str(FONT), size)
    while font.getlength(text) > w * 0.92 and size > 20:
        size = int(size * 0.92)
        font = ImageFont.truetype(str(FONT), size)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    tw = font.getlength(text)
    e = ease_out_cubic(progress)
    y = int(h * 0.20 + (1 - e) * h * 0.06)
    d.text(((w - tw) / 2, y), text, font=font, fill=(255, 255, 255, int(255 * e)))
    a = np.asarray(img, np.float32) / 255.0
    return a[..., :3], a[..., 3:4]


def _envelope(t: float, t0: float, t1: float, ramp: float = 0.25) -> float:
    if t < t0 or t > t1:
        return 0.0
    return min(1.0, (t - t0) / ramp, (t1 - t) / ramp) if ramp > 0 else 1.0


def apply(frame: np.ndarray, m: np.ndarray, op: Op, t: float, cache: dict) -> np.ndarray:
    """frame: BGR uint8; m: float matte 0..1 of the op's target."""
    body = op.body
    env = _envelope(t, op.t0, op.t1)
    if env <= 0:
        return frame
    f = frame.astype(np.float32)
    m3 = (m * env)[..., None]
    if isinstance(body, Blur):
        if body.pixelate:
            small = cv2.resize(frame, (max(1, frame.shape[1] // 28), max(1, frame.shape[0] // 28)), interpolation=cv2.INTER_LINEAR)
            fx = cv2.resize(small, (frame.shape[1], frame.shape[0]), interpolation=cv2.INTER_NEAREST).astype(np.float32)
        else:
            sigma = 6 + 30 * body.strength
            fx = cv2.GaussianBlur(frame, (0, 0), sigma).astype(np.float32)
        out = f * (1 - m3) + fx * m3
    elif isinstance(body, Spotlight):
        gray = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR).astype(np.float32)
        bg = gray * (1 - 0.35 * body.amount)
        k = env * body.amount
        bgmix = f * (1 - k) + bg * k
        mm = m[..., None]
        out = bgmix * (1 - mm) + f * mm
    elif isinstance(body, TitleBehind):
        prog = min(1.0, (t - op.t0) / 0.7)
        key = (body.text, round(prog, 2))
        if key not in cache:
            cache.clear()
            cache[key] = _title_layer(body.text, frame.shape[1], frame.shape[0], prog)
        rgb, a = cache[key]
        fade_out = min(1.0, max(0.0, (op.t1 - t) / 0.3))
        a = a * fade_out
        behind = f * (1 - a) + (rgb[..., ::-1] * 255.0) * a      # title over the plate
        mm = m[..., None]
        out = behind * (1 - mm) + f * mm                           # subject back on top
    else:
        return frame
    return np.clip(out, 0, 255).astype(np.uint8)


def render(proxy: Path, out: Path, ops: list[tuple[Op, dict[int, dict[str, sam.Track]], str]],
           fps: float, timeout_s: int = 1800) -> Path:
    """Render every op (op, its target's tracks, object id) over the proxy; mux the proxy's audio."""
    layer = {"spotlight": 0, "blur": 1, "title_behind": 2}       # grade -> redact -> type on top
    ops = sorted(ops, key=lambda o: layer.get(o[0].body.kind, 9))
    cap = cv2.VideoCapture(str(proxy))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    mattes = [Matte(tr, oid, w, h) for (_, tr, oid) in ops]
    caches: list[dict] = [{} for _ in ops]
    enc = subprocess.Popen(
        ["timeout", "-k", "5", str(timeout_s), "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
         "-i", str(proxy), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-threads", "2", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i / fps
            for (op, _, _), mt, c in zip(ops, mattes, caches):
                if op.t0 - 0.5 <= t <= op.t1 + 0.5:      # keep EMA warm around the range
                    frame = apply(frame, mt.at(i), op, t, c)
            enc.stdin.write(frame.tobytes())
            i += 1
    finally:
        cap.release()
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"encode failed ({enc.returncode})")
    return out
