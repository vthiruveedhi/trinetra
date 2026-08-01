"""
OpenCV HOG person detector — host PC dogfood only.

Not the product pin (YOLO11n). Use when real ONNX weights are not present
so a live webcam can still produce occupancy events.
"""

from __future__ import annotations

from typing import List

import numpy as np

from .backend import Detection, InferenceBackend


class HogPersonBackend(InferenceBackend):
    """CPU HOG + SVM pedestrian detector (OpenCV)."""

    model_version = "opencv-hog-person-v1"

    def __init__(
        self,
        conf: float = 0.5,
        hit_threshold: float = 0.0,
        scale: float = 1.05,
        win_stride: tuple[int, int] = (8, 8),
    ) -> None:
        import cv2

        self._cv2 = cv2
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.conf = conf
        self.hit_threshold = hit_threshold
        self.scale = scale
        self.win_stride = win_stride

    def predict(self, frame_bgr: np.ndarray) -> List[Detection]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        # HOG is slow on large frames — downscale for detect, scale boxes back
        h, w = frame_bgr.shape[:2]
        max_w = 640
        if w > max_w:
            scale = max_w / float(w)
            small = self._cv2.resize(frame_bgr, (int(w * scale), int(h * scale)))
        else:
            scale = 1.0
            small = frame_bgr

        rects, weights = self._hog.detectMultiScale(
            small,
            winStride=self.win_stride,
            padding=(8, 8),
            scale=self.scale,
            hitThreshold=self.hit_threshold,
        )
        dets: List[Detection] = []
        if rects is None or len(rects) == 0:
            return dets
        inv = 1.0 / scale
        for i, (x, y, bw, bh) in enumerate(rects):
            weight = float(weights[i][0]) if weights is not None and len(weights) > i else 1.0
            # Map SVM weight to a soft confidence in ~[0,1]
            conf = float(1.0 / (1.0 + np.exp(-weight)))
            if conf < self.conf and weight < 0.3:
                continue
            x1 = float(x) * inv
            y1 = float(y) * inv
            x2 = float(x + bw) * inv
            y2 = float(y + bh) * inv
            dets.append(
                Detection(
                    class_id=0,
                    class_name="person",
                    conf=max(conf, 0.35),
                    xyxy=(x1, y1, x2, y2),
                )
            )
        return dets
