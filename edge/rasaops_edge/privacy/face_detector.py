"""Face detector used solely for redaction (not recognition / embeddings)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np


class FaceDetectorError(RuntimeError):
    """Raised on detector hard-fail, timeout, or unloadable model."""


@dataclass(frozen=True)
class FaceBox:
    """Axis-aligned face box in absolute pixels. No landmarks / embeddings."""

    xyxy: Tuple[float, float, float, float]
    conf: float

    def clamp(self, width: int, height: int) -> "FaceBox":
        x1, y1, x2, y2 = self.xyxy
        x1 = max(0.0, min(float(width), x1))
        y1 = max(0.0, min(float(height), y1))
        x2 = max(0.0, min(float(width), x2))
        y2 = max(0.0, min(float(height), y2))
        return FaceBox(xyxy=(x1, y1, x2, y2), conf=self.conf)


class FaceDetector(ABC):
    """Predict face boxes for redaction only."""

    model_version: str = "unknown"

    @abstractmethod
    def detect_faces(self, frame_bgr: np.ndarray) -> List[FaceBox]:
        ...


class MockFaceDetector(FaceDetector):
    """
    Deterministic face detector for unit tests and goldens.

    - ``faces``: fixed boxes returned every call
    - ``faces_by_call``: sequence of results per call index
    - ``raise_error``: simulate hard-fail / timeout
    """

    model_version = "mock-face-v1"

    def __init__(
        self,
        faces: Optional[Sequence[FaceBox]] = None,
        faces_by_call: Optional[Sequence[Sequence[FaceBox]]] = None,
        raise_error: bool = False,
        error_message: str = "face detector hard-fail",
    ) -> None:
        self._faces = list(faces or [])
        self._seq = [list(s) for s in (faces_by_call or [])]
        self._raise_error = raise_error
        self._error_message = error_message
        self._i = 0

    def detect_faces(self, frame_bgr: np.ndarray) -> List[FaceBox]:
        if self._raise_error:
            raise FaceDetectorError(self._error_message)
        if self._seq:
            idx = min(self._i, len(self._seq) - 1)
            self._i += 1
            return list(self._seq[idx])
        return list(self._faces)

    def reset(self) -> None:
        self._i = 0
