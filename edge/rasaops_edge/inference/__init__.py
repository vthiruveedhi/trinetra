"""Inference backends (PR-04)."""

from .backend import Detection, InferenceBackend
from .factory import create_backend
from .mock import MockInferenceBackend
from .onnx_yolo import OnnxYoloBackend

__all__ = [
    "Detection",
    "InferenceBackend",
    "MockInferenceBackend",
    "OnnxYoloBackend",
    "create_backend",
]
