"""Inference backend abstraction (KD-3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class Detection:
    """Single detection. No landmarks / embeddings (privacy)."""

    class_id: int
    class_name: str
    conf: float
    xyxy: Tuple[float, float, float, float]  # absolute pixels in source frame
    track_id: Optional[int] = None  # filled by tracker; short-lived anonymous only

    @property
    def centroid(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    def with_track(self, track_id: int) -> "Detection":
        return Detection(
            class_id=self.class_id,
            class_name=self.class_name,
            conf=self.conf,
            xyxy=self.xyxy,
            track_id=track_id,
        )


class InferenceBackend(ABC):
    """Predict detections for a BGR frame (H, W, 3) uint8."""

    model_version: str = "unknown"

    @abstractmethod
    def predict(self, frame_bgr: np.ndarray) -> List[Detection]:
        ...

    def warmup(self, shape: Sequence[int] = (320, 320, 3)) -> None:
        h, w = int(shape[0]), int(shape[1])
        dummy = np.zeros((h, w, 3), dtype=np.uint8)
        self.predict(dummy)
