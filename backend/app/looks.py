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


# ---------------------------------------------------------------------------------------------
# v2 looks (session 3): built to read at phone size, not just on a monitor.
# ---------------------------------------------------------------------------------------------

def _srgb2lin(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _lin2srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def _shoulder(x: np.ndarray, knee: float = 0.6) -> np.ndarray:
    """Soft highlight roll-off: identity below the knee, asymptotic to 1 above it."""
    return np.where(x < knee, x, knee + (1 - knee) * (1 - np.exp(-(x - knee) / (1 - knee))))


def _lum(x: np.ndarray) -> np.ndarray:            # BGR, linear
    return (0.2126 * x[..., 2] + 0.7152 * x[..., 1] + 0.0722 * x[..., 0])[..., None]


def main_object(fg: np.ndarray) -> np.ndarray:
    """Largest connected component plus fragments near it (drops SAM blips elsewhere in frame)."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 2:
        return fg
    big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])); x, y, ww, hh = st[big, :4]
    keep = [k for k in range(1, n) if k == big or (abs(st[k, 0] + st[k, 2] / 2 - (x + ww / 2)) < ww
                                                  and abs(st[k, 1] + st[k, 3] / 2 - (y + hh / 2)) < hh)]
    return np.isin(lab, keep).astype(np.uint8)


def redact_mosaic(frame: np.ndarray, fg: np.ndarray, amount: float = 1.0, block: int = 12, grow: int = 5,
                  frost: float = 0.4, contrast: float = 2.6, anchor: tuple[float, float] | None = None) -> np.ndarray:
    """Deliberate privacy redaction: a frosted pixel mosaic over the object, feathered ~1.6 px.

    - The mosaic is averaged from the object's own (grown) region only, so fingers never smear in.
    - Skin-coloured pixels next to the object (the fingers holding it) are cut out of the patch and
      stay sharp.
    - Cell contrast is boosted and the patch lifted ("frost") so it reads as redacted even when the
      object is a dark phone back, at thumbnail size.
    - `anchor` (a smoothed object centre) locks the block grid to the object so cells don't crawl."""
    if amount <= 0 or not fg.any():
        return frame
    h, w = frame.shape[:2]
    obj = cv2.morphologyEx(main_object(fg), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    region = cv2.dilate(obj, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1,) * 2))
    ycc = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
    skin = ((ycc[..., 1] >= 133) & (ycc[..., 1] <= 180) & (ycc[..., 2] >= 85) & (ycc[..., 2] <= 130)
            & (ycc[..., 0] > 30)).astype(np.uint8)
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fingers = cv2.dilate(skin & (1 - obj) & region, np.ones((3, 3), np.uint8)) & (1 - obj)
    region = region & (1 - fingers)

    F = frame.astype(np.float32)
    if anchor is None:
        ys, xs = np.nonzero(obj); anchor = (xs.mean(), ys.mean())
    ox, oy = int(anchor[0]) % block, int(anchor[1]) % block
    py, px = (block - oy) % block, (block - ox) % block
    rm = region.astype(np.float32)
    Fp = np.pad(F * rm[..., None], ((py, block), (px, block), (0, 0)))
    Mp = np.pad(rm, ((py, block), (px, block)))
    H2, W2 = (Mp.shape[0] // block) * block, (Mp.shape[1] // block) * block
    sF = Fp[:H2, :W2].reshape(H2 // block, block, W2 // block, block, 3).sum((1, 3))
    sM = Mp[:H2, :W2].reshape(H2 // block, block, W2 // block, block).sum((1, 3))
    cells = sF / (sM[..., None] + 1e-4)
    sel = sM > block * block * 0.25
    if sel.any():
        mu = cells[sel].mean(0)
        cells = np.where(sel[..., None], mu + (cells - mu) * contrast, cells)
    mos = np.repeat(np.repeat(cells, block, 0), block, 1)[py:py + h, px:px + w]
    mos = mos * 0.75 + mos.mean(-1, keepdims=True) * 0.25
    mos = mos * (1 - frost) + 255 * 0.75 * frost
    a = cv2.GaussianBlur(rm, (0, 0), 1.6) * (1 - cv2.GaussianBlur(fingers.astype(np.float32), (0, 0), 0.8))
    a = (a * amount)[..., None]
    return np.clip(F * (1 - a) + mos * a, 0, 255).astype(np.uint8)


def mosaic_block(fgs: list[np.ndarray], cells: float = 11.0, lo: int = 12, hi: int = 64) -> int:
    """One block size per op, from the target's median size: about `cells` blocks across the object, so a
    phone filling the frame is as unreadable as a phone across the street (fixed 12 px left a close-up legible)."""
    sizes = [np.sqrt(float(f.sum())) for f in fgs if f.any()]
    if not sizes:
        return lo
    return int(np.clip(round(float(np.median(sizes)) / cells), lo, hi))


def smooth_anchors(fgs: list[np.ndarray], radius: int = 4) -> list[tuple[float, float] | None]:
    """Object centres per frame, box-smoothed over +-radius frames (grid lock for redact_mosaic)."""
    c = []
    for f in fgs:
        if f.any():
            ys, xs = np.nonzero(main_object(f)); c.append((xs.mean(), ys.mean()))
        else:
            c.append(None)
    out = []
    for i in range(len(c)):
        win = [p for p in c[max(0, i - radius): i + radius + 1] if p is not None]
        out.append((float(np.mean([p[0] for p in win])), float(np.mean([p[1] for p in win]))) if win else None)
    return out


def make_pop_v2(frame: np.ndarray, fg: np.ndarray, amount: float = 1.0, subj_stops: float = 0.85,
                bg_stops: float = -1.1, desat: float = 0.62, defocus: float = 7.0, warm: float = 0.07,
                rim: float = 0.5, wrap: float = 0.25, vignette: float = 0.45) -> np.ndarray:
    """"Make him pop" that reads on a phone: subject brighter (+0.85 stop) and warmer, background
    desaturated, darker (-1.1 stop plus a subject-centred vignette) and defocused, for a measured
    ~2.2-stop swing in subject/background separation on Mixkit #134.

    Graded in linear light. The defocus is matte-aware (subject pixels excluded) so no dark halo is
    smeared into the background; the matte's soft edge is decontaminated (sky colour replaced by the
    subject's interior colour) before the lift, then a thin warm rim (scaled by how bright the
    original backlight was) and a light wrap from the graded background sit the subject in the plate."""
    if amount <= 0:
        return frame
    h, w = frame.shape[:2]
    m = refine_matte(frame, fg) if fg.any() else np.zeros((h, w), np.float32)
    F = frame.astype(np.float32) / 255.0
    L = _srgb2lin(F)
    keep = 1 - cv2.dilate(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))).astype(np.float32)

    def nblur(s: float):
        d = cv2.GaussianBlur(keep, (0, 0), s)[..., None]
        return cv2.GaussianBlur(L * keep[..., None], (0, 0), s) / (d + 1e-4), d

    bgL, den = nblur(defocus)
    wide, _ = nblur(defocus * 4)
    a = np.clip(den / 0.15, 0, 1)
    bgL = bgL * a + wide * (1 - a)
    bgL = bgL * (1 - desat) + _lum(bgL) * desat
    if fg.any():
        ys, xs = np.nonzero(fg); cx, cy = xs.mean(), ys.mean() * 0.8
    else:
        cx, cy = w / 2, h / 2
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    vig = 1 - vignette * np.clip((d - 0.12) / 0.55, 0, 1) ** 1.4
    bgL = bgL * (2 ** bg_stops) * vig[..., None]

    # edge decontamination: in the matte's soft zone use the subject's interior colour, not the sky
    s = np.clip((m - 0.45) / 0.45, 0, 1)[..., None]; s = s * s * (3 - 2 * s)
    core = cv2.erode((m > 0.85).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    cd = cv2.GaussianBlur(core, (0, 0), 3)[..., None]
    fill = cv2.GaussianBlur(L * core[..., None], (0, 0), 3) / (cd + 1e-4)
    cw = np.clip(cd / 0.05, 0, 1)
    Ls = L * s + (fill * cw + L * (1 - cw)) * (1 - s)

    sL = _shoulder(Ls * (2 ** subj_stops) * np.array([1 - warm, 1.0, 1 + warm], np.float32))
    mh = np.clip((m - 0.5) * 4, 0, 1)
    er = cv2.erode(mh, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    band = cv2.GaussianBlur(np.clip(mh - er, 0, 1), (0, 0), 2.2) * mh
    envY = _lum(nblur(20)[0])
    top = np.clip(1.2 - yy / h, 0.3, 1)[..., None]
    warmwhite = np.array([0.72, 0.88, 1.0], np.float32)
    sL = sL + rim * band[..., None] * top * np.clip(envY * 1.5, 0.08, 0.6) * warmwhite
    wb = (cv2.GaussianBlur(1 - m, (0, 0), 5) * m)[..., None] * wrap
    sL = sL * (1 - wb) + _lum(bgL) * warmwhite * 1.6 * wb

    out = _lin2srgb(bgL * (1 - m[..., None]) + sL * m[..., None])
    out = F * (1 - amount) + out * amount
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


def separation_stops(frame: np.ndarray, fg: np.ndarray) -> float:
    """log2(mean subject luminance / mean background luminance), linear light. QA metric for spotlight."""
    L = _srgb2lin(frame.astype(np.float32) / 255.0); Y = _lum(L)[..., 0]
    bg = cv2.dilate(fg, np.ones((25, 25), np.uint8)) == 0
    return float(np.log2(Y[fg > 0].mean() + 1e-6) - np.log2(Y[bg].mean() + 1e-6))
