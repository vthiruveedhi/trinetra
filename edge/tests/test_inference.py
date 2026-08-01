from pathlib import Path

import numpy as np

from rasaops_edge.inference.factory import create_backend
from rasaops_edge.inference.mock import MockInferenceBackend, person
from rasaops_edge.inference.onnx_yolo import OnnxYoloBackend

ROOT = Path(__file__).resolve().parents[2]
TINY = ROOT / "edge" / "models" / "tiny_yolo_like_320.onnx"


def test_mock_predict():
    det = person((10, 10, 50, 80))
    be = MockInferenceBackend(default=[det])
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    out = be.predict(frame)
    assert len(out) == 1
    assert out[0].class_name == "person"


def test_factory_mock_env(monkeypatch):
    monkeypatch.setenv("RASAOPS_INFERENCE_BACKEND", "mock")
    be = create_backend()
    assert isinstance(be, MockInferenceBackend)


def test_onnx_tiny_if_present():
    if not TINY.exists():
        return  # optional artifact
    be = OnnxYoloBackend(TINY, input_size=320, conf_thres=0.2, keep_classes=None)
    frame = np.zeros((320, 320, 3), dtype=np.uint8)
    dets = be.predict(frame)
    assert be.model_version == TINY.name
    # tiny model injects a high person score near center of letterbox
    assert len(dets) >= 1
    assert dets[0].class_name == "person"
    assert dets[0].conf >= 0.5
