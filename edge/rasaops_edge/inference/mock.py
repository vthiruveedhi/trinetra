"""Deterministic mock detector for tests and dogfood without ONNX weights."""

from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np

from .backend import Detection, InferenceBackend


class MockInferenceBackend(InferenceBackend):
    """
    Returns configured detections, optionally keyed by synthetic time step.

    Use for unit tests and CI when yolo11n weights are not present.
    """

    model_version = "mock-v1"

    def __init__(
        self,
        detections_by_frame: Optional[Sequence[Sequence[Detection]]] = None,
        default: Optional[Sequence[Detection]] = None,
    ) -> None:
        self._seq = [list(d) for d in (detections_by_frame or [])]
        self._default = list(default or [])
        self._i = 0

    def predict(self, frame_bgr: np.ndarray) -> List[Detection]:
        if self._seq:
            idx = min(self._i, len(self._seq) - 1)
            dets = list(self._seq[idx])
            self._i += 1
            return dets
        return list(self._default)

    def reset(self) -> None:
        self._i = 0


def person(xyxy: tuple[float, float, float, float], conf: float = 0.9) -> Detection:
    return Detection(class_id=0, class_name="person", conf=conf, xyxy=xyxy)
