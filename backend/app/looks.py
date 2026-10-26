"""Premium tracked looks on real SAM mattes: redaction blur and "make him pop".

Both reuse title_fx.refine_matte (guided filter in a thin band around SAM's edge) so the
effect boundary follows the plate's real edge, and both ease in so the change reads on screen.
"""
from __future__ import annotations
import cv2
import numpy as np

from .title_fx import refine_matte


def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def redact_blur(frame: np.ndarray, fg: np.ndarray, amount: float = 1.0, grow_px: int = 7, sigma: float = 16.0) -> np.ndarray:
    """Blur an object (a phone, a logo, a plate). The matte is grown so the blur covers the object's
    silhouette fully even on fast motion, and the blur is a normalised (masked) blur so neighbouring
    pixels (fingers, shirt) don't bleed into the redacted area."""
    if amount <= 0 or not fg.any():
        return frame
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1,) * 2)
    grown = cv2.dilate(fg, k)
    m = cv2.GaussianBlur(grown.astype(np.float32), (0, 0), 2.0) * amount
    F = frame.astype(np.float32)
    w = cv2.GaussianBlur(grown.astype(np.float32), (0, 0), sigma) + 1e-4
    inside = cv2.GaussianBlur(F * grown[..., None], (0, 0), sigma) / w[..., None]
    out = F * (1 - m[..., None]) + inside * m[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def make_pop(frame: np.ndarray, fg: np.ndarray, amount: float = 1.0, desat: float = 0.45, dim: float = 0.18,
             defocus: float = 3.0, vignette: float = 0.30, subject_lift: float = 0.06) -> np.ndarray:
    """Subject stays in full colour and gets a touch of contrast; the background is pulled down:
    desaturated, dimmed, softened (fake shallow depth of field) and vignetted around the subject."""
    if amount <= 0:
        return frame
    h, w = frame.shape[:2]
    m = refine_matte(frame, fg) if fg.any() else np.zeros((h, w), np.float32)
    F = frame.astype(np.float32) / 255.0
    bg = cv2.GaussianBlur(F, (0, 0), defocus) if defocus else F
    gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)[..., None]
    bg = bg * (1 - desat) + gray * desat
    # vignette centred on the subject's centroid
    if fg.any():
        ys, xs = np.nonzero(fg); cx, cy = xs.mean(), ys.mean()
    else:
        cx, cy = w / 2, h / 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    vig = 1 - vignette * np.clip((d - 0.15) / 0.6, 0, 1) ** 1.5
    bg = bg * (1 - dim) * vig[..., None]
    subj = np.clip((F - 0.5) * (1 + subject_lift) + 0.5 + subject_lift * 0.15, 0, 1)
    looked = bg * (1 - m[..., None]) + subj * m[..., None]
    out = F * (1 - amount) + looked * amount
    return (np.clip(out, 0, 1) * 255).astype(np.uint8)
