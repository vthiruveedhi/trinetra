"""Fail-closed privacy scrubber (PR-06). Face detector is redaction-only — never FR."""

from .face_detector import FaceBox, FaceDetector, FaceDetectorError, MockFaceDetector
from .scrubber import PrivacyConfig, PrivacyScrubber, ScrubResult

__all__ = [
    "FaceBox",
    "FaceDetector",
    "FaceDetectorError",
    "MockFaceDetector",
    "PrivacyConfig",
    "PrivacyScrubber",
    "ScrubResult",
]
