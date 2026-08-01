from rasaops_edge.capture.webcam_source import WebcamSource
from rasaops_edge.inference.factory import create_backend
from rasaops_edge.inference.hog_person import HogPersonBackend
from rasaops_edge.inference.mock import MockInferenceBackend


def test_webcam_source_construct():
    s = WebcamSource(device=0, target_fps=2.0, width=640, height=480)
    assert s.target_fps == 2.0
    assert s.device == 0


def test_webcam_from_env(monkeypatch):
    monkeypatch.setenv("RASAOPS_CAMERA_INDEX", "1")
    monkeypatch.setenv("RASAOPS_CAPTURE_FPS", "3.5")
    monkeypatch.setenv("RASAOPS_CAMERA_WIDTH", "640")
    s = WebcamSource.from_env()
    assert s.device == 1
    assert s.target_fps == 3.5
    assert s.width == 640


def test_factory_hog_explicit(monkeypatch):
    monkeypatch.setenv("RASAOPS_INFERENCE_BACKEND", "hog")
    be = create_backend()
    assert isinstance(be, HogPersonBackend)


def test_factory_mock_explicit(monkeypatch):
    monkeypatch.setenv("RASAOPS_INFERENCE_BACKEND", "mock")
    be = create_backend()
    assert isinstance(be, MockInferenceBackend)


def test_hog_predict_empty():
    import numpy as np

    be = HogPersonBackend(conf=0.9)
    dets = be.predict(np.zeros((240, 320, 3), dtype=np.uint8))
    assert dets == []
