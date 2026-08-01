"""Backend factory: host ultralytics 640 for recall; ONNX 320 for Pi product path."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from .backend import InferenceBackend
from .mock import MockInferenceBackend
from .onnx_yolo import ATMOSPHERE_CLASS_ALLOW, MVP_CLASS_ALLOW, OnnxYoloBackend

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ONNX = _REPO_ROOT / "edge" / "models" / "yolo11n_int8_320.onnx"
_DEFAULT_PT = _REPO_ROOT / "edge" / "models" / "yolo11n.pt"
_TINY_MODEL = _REPO_ROOT / "edge" / "models" / "tiny_yolo_like_320.onnx"


def _is_real_onnx(path: Path) -> bool:
    name = path.name.lower()
    if "tiny_yolo_like" in name:
        return False
    return path.exists() and path.suffix.lower() == ".onnx"


def _want_host_high_recall() -> bool:
    """Lab hosts prefer imgsz 640 ultralytics when weights exist."""
    flag = os.environ.get("RASAOPS_HOST_HIGH_RECALL", "1").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return _DEFAULT_PT.exists()
    return False


def create_backend(
    backend: Optional[str] = None,
    model_path: Optional[str | Path] = None,
    input_size: int = 320,
    keep_classes: Optional[frozenset[str]] = None,
    atmosphere: bool = False,
) -> InferenceBackend:
    """
    Resolve inference backend.

    Priority:
      1. RASAOPS_INFERENCE_BACKEND env (mock|onnx|hog|ultralytics|auto)
      2. explicit backend arg
      3. auto:
         - host high-recall: ultralytics yolo11n.pt @ imgsz 640 (if present)
         - else product ONNX 320
         - else HOG → mock
    """
    choice = (backend or os.environ.get("RASAOPS_INFERENCE_BACKEND") or "auto").lower()
    path = Path(
        model_path
        or os.environ.get("RASAOPS_MODEL_PATH")
        or _DEFAULT_ONNX
    )
    atmo = atmosphere or os.environ.get("RASAOPS_ATMOSPHERE", "").lower() in (
        "1",
        "true",
        "yes",
    )
    keep = keep_classes or (ATMOSPHERE_CLASS_ALLOW if atmo else MVP_CLASS_ALLOW)
    imgsz = int(os.environ.get("RASAOPS_IMGSZ") or 640)
    conf = float(os.environ.get("RASAOPS_CONF") or 0.15)

    if choice == "mock":
        return MockInferenceBackend()

    if choice == "hog":
        from .hog_person import HogPersonBackend

        return HogPersonBackend()

    if choice in ("ultralytics", "yolo", "pt"):
        from .ultralytics_yolo import UltralyticsYoloBackend

        pt = Path(model_path) if model_path else _DEFAULT_PT
        if pt.suffix.lower() == ".onnx":
            pt = _DEFAULT_PT
        return UltralyticsYoloBackend(
            pt, imgsz=imgsz, conf_thres=conf, keep_classes=keep
        )

    if choice == "onnx":
        return OnnxYoloBackend(
            path if path.suffix.lower() == ".onnx" else _DEFAULT_ONNX,
            input_size=input_size,
            conf_thres=float(os.environ.get("RASAOPS_CONF") or 0.18),
            keep_classes=keep,
        )

    # auto
    if _want_host_high_recall():
        try:
            from .ultralytics_yolo import UltralyticsYoloBackend

            log.info(
                "auto backend: Ultralytics host high-recall imgsz=%s conf=%s",
                imgsz,
                conf,
            )
            return UltralyticsYoloBackend(
                _DEFAULT_PT, imgsz=imgsz, conf_thres=conf, keep_classes=keep
            )
        except Exception as exc:
            log.warning("Ultralytics unavailable (%s); falling back to ONNX", exc)

    if _is_real_onnx(path):
        return OnnxYoloBackend(path, input_size=input_size, keep_classes=keep)
    if _is_real_onnx(_DEFAULT_ONNX):
        return OnnxYoloBackend(_DEFAULT_ONNX, input_size=input_size, keep_classes=keep)

    try:
        from .hog_person import HogPersonBackend

        return HogPersonBackend()
    except Exception:
        if _TINY_MODEL.exists():
            return OnnxYoloBackend(_TINY_MODEL, input_size=input_size)
        return MockInferenceBackend()
