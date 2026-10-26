"""Premium "title behind the subject": camera-locked type, edge-aware mattes, contact shadow, light wrap.

What makes it read as a real composite rather than a sticker:
  * world lock: the title rides the background's own motion (LK flow on non-subject pixels ->
    similarity transform per frame, accumulated and smoothed), so it sits *in* the skyline
  * edge-aware matte: SAM's binary mask is refined with a guided filter inside a thin band around
    its edge, so defocused or motion-blurred edges get a matching soft alpha (no cut-out look)
  * no temporal EMA on a moving subject (EMA lags the matte and leaks type over the leading edge)
  * contact shadow: the subject's matte, offset and blurred, darkens the type just behind them
  * light wrap: a little of the type's light spills onto the subject's rim
  * staggered glyph rise (ease-out cubic), 0.6 px softening to match plate sharpness
"""
from __future__ import annotations
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).parent / "fonts" / "Anton-Regular.ttf"
WARM_BGR = np.array([238, 242, 246], np.float32) / 255.0


def _smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2); k /= k.sum()
    xp = np.pad(x, (r, r), mode="edge")
    return np.convolve(xp, k, mode="valid")


def camera_track(frames: list[np.ndarray], fg: list[np.ndarray], smooth_sigma: float = 2.0) -> np.ndarray:
    """Per-frame 3x3 similarity transforms mapping frame-0 coords -> frame-f coords, background only."""
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    Ms = [np.eye(3)]
    k = np.ones((31, 31), np.uint8)
    for f in range(1, len(frames)):
        ex = cv2.dilate(np.maximum(fg[f - 1], fg[f]), k)
        allowed = ((1 - ex) * 255).astype(np.uint8)
        p0 = cv2.goodFeaturesToTrack(gray[f - 1], 600, 0.01, 8, mask=allowed)
        A = None
        if p0 is not None and len(p0) >= 8:
            p1, st, _ = cv2.calcOpticalFlowPyrLK(gray[f - 1], gray[f], p0, None, winSize=(21, 21), maxLevel=3)
            g = st.ravel() == 1
            if g.sum() >= 8:
                A, _ = cv2.estimateAffinePartial2D(p0[g], p1[g], method=cv2.RANSAC, ransacReprojThreshold=1.5)
        step = np.vstack([A, [0, 0, 1]]) if A is not None else np.eye(3)
        Ms.append(step @ Ms[-1])
    M = np.array(Ms)
    for a in range(2):
        for b in range(3):
            M[:, a, b] = _smooth(M[:, a, b], smooth_sigma)
    return M


def guided_filter(guide_bgr: np.ndarray, p: np.ndarray, r: int = 5, eps: float = 2e-3) -> np.ndarray:
    I = cv2.cvtColor(guide_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    bf = lambda x: cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1))
    mI, mp = bf(I), bf(p)
    a = (bf(I * p) - mI * mp) / (bf(I * I) - mI * mI + eps)
    b = mp - a * mI
    return np.clip(bf(a) * I + bf(b), 0, 1)


def refine_matte(frame: np.ndarray, fg: np.ndarray, band_px: int = 4) -> np.ndarray:
    """Binary SAM mask (uint8 0/1) -> soft float alpha that follows the plate's real edge."""
    p = fg.astype(np.float32)
    k = np.ones((2 * band_px + 1,) * 2, np.uint8)
    band = cv2.dilate(fg, k) - cv2.erode(fg, k)
    m = np.where(band > 0, guided_filter(frame, p), p)
    return cv2.GaussianBlur(m, (0, 0), 0.7)


def title_alpha(text: str, w: int, h: int, t_rel: float, size: int = 190, top: float = 0.10,
                tracking: float = 0.02, stagger: float = 0.045, dur: float = 0.55) -> np.ndarray:
    """Title alpha (0..1) with a staggered per-glyph rise; t_rel = seconds since the title starts."""
    font = ImageFont.truetype(str(FONT), size)
    widths = [font.getlength(c) for c in text]
    sp = size * tracking
    x = (w - (sum(widths) + sp * (len(text) - 1))) / 2
    acc = np.zeros((h, w), np.uint8)
    for i, c in enumerate(text):
        p = min(1.0, max(0.0, (t_rel - i * stagger) / dur))
        e = 1 - (1 - p) ** 3
        if p > 0 and c != " ":
            g = Image.new("L", (w, h), 0)
            ImageDraw.Draw(g).text((x, h * top + (1 - e) * size * 0.35), c, font=font, fill=int(255 * e))
            acc = np.maximum(acc, np.asarray(g))
        x += widths[i] + sp
    return acc.astype(np.float32) / 255.0


def composite(frame: np.ndarray, title_a: np.ndarray, fg: np.ndarray, opacity: float = 0.96,
              shadow: float = 0.38, wrap: float = 0.2) -> np.ndarray:
    F = frame.astype(np.float32) / 255.0
    m = refine_matte(frame, fg)
    a = cv2.GaussianBlur(title_a, (0, 0), 0.6) * opacity
    col = np.broadcast_to(WARM_BGR, F.shape).copy()
    if shadow:
        sh = cv2.GaussianBlur(np.roll(np.roll(fg.astype(np.float32), 14, 1), 8, 0), (0, 0), 10)
        col *= (1 - shadow * sh[..., None])
    behind = F * (1 - a[..., None]) + col * a[..., None]
    out = behind * (1 - m[..., None]) + F * m[..., None]
    if wrap:
        edge = np.clip(m - cv2.erode(m, np.ones((7, 7), np.uint8)), 0, 1)
        out += (edge * cv2.GaussianBlur(a, (0, 0), 6) * wrap)[..., None] * (WARM_BGR - out)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)


def render_title_behind(frames: list[np.ndarray], fg: list[np.ndarray], text: str, fps: float, out: Path,
                        t0: float = 0.8, ref_frame: int | None = None, crf: int = 18, timeout_s: int = 900,
                        **title_kw) -> Path:
    h, w = frames[0].shape[:2]
    M = camera_track(frames, fg)
    ref = len(frames) // 2 if ref_frame is None else ref_frame
    enc = subprocess.Popen(
        ["timeout", "-k", "5", str(timeout_s), "nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-threads", "4",
         "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    still = None
    try:
        for i, fr in enumerate(frames):
            t = i / fps
            if t < t0:
                enc.stdin.write(fr.tobytes()); continue
            tr = t - t0
            if tr > 1.2 and still is not None:
                a0 = still
            else:
                a0 = title_alpha(text, w, h, tr, **title_kw)
                if tr > 1.2:
                    still = a0
            T = M[i] @ np.linalg.inv(M[ref])
            a = cv2.warpAffine(a0, T[:2], (w, h), flags=cv2.INTER_LINEAR)
            enc.stdin.write(composite(fr, a, fg[i]).tobytes())
    finally:
        enc.stdin.close(); enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"encode failed ({enc.returncode})")
    return out
