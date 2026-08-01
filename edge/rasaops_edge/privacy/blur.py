"""Pixel redaction helpers (blur / pixelate). No identity features stored."""

from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np

Box = Tuple[float, float, float, float]


def _clip_box(xyxy: Box, width: int, height: int) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = xyxy
    ix1 = int(max(0, min(width, round(x1))))
    iy1 = int(max(0, min(height, round(y1))))
    ix2 = int(max(0, min(width, round(x2))))
    iy2 = int(max(0, min(height, round(y2))))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    return ix1, iy1, ix2, iy2


def blur_regions(
    frame_bgr: np.ndarray,
    boxes: Iterable[Box],
    *,
    ksize: int = 31,
) -> np.ndarray:
    """
    Return a copy of ``frame_bgr`` with each region strongly blurred.

    Uses OpenCV GaussianBlur when available; falls back to box mean fill.
    """
    out = np.array(frame_bgr, copy=True)
    if out.ndim != 3 or out.shape[2] != 3:
        raise ValueError("frame_bgr must be HxWx3")
    h, w = out.shape[:2]
    k = ksize if ksize % 2 == 1 else ksize + 1
    k = max(3, k)

    try:
        import cv2
    except ImportError:  # pragma: no cover
        cv2 = None

    for box in boxes:
        clipped = _clip_box(box, w, h)
        if clipped is None:
            continue
        x1, y1, x2, y2 = clipped
        roi = out[y1:y2, x1:x2]
        if roi.size == 0:
            continue
        if cv2 is not None:
            # Kernel cannot exceed ROI size
            rk = min(k, max(3, (min(roi.shape[0], roi.shape[1]) // 2) * 2 + 1))
            out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (rk, rk), 0)
        else:  # pragma: no cover
            mean = roi.mean(axis=(0, 1), keepdims=True).astype(roi.dtype)
            out[y1:y2, x1:x2] = mean
    return out


def head_roi_from_person(
    person_xyxy: Box,
    *,
    head_roi_frac: float = 0.30,
    min_side_px: float = 8.0,
) -> Box | None:
    """
    Top ``head_roi_frac`` of person box height as head-ROI heuristic.

    Returns None if the person box is invalid or too small to estimate a head.
    """
    x1, y1, x2, y2 = person_xyxy
    pw = x2 - x1
    ph = y2 - y1
    if pw <= 0 or ph <= 0:
        return None
    if min(pw, ph) < min_side_px:
        return None
    head_h = ph * head_roi_frac
    if head_h < min_side_px * 0.5:
        return None
    return (x1, y1, x2, y1 + head_h)
