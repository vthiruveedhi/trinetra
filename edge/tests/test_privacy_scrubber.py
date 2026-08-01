"""PR-06 golden fixtures for fail-closed privacy scrubber."""

from __future__ import annotations

import numpy as np
import pytest

from rasaops_edge.privacy import (
    FaceBox,
    MockFaceDetector,
    PrivacyConfig,
    PrivacyScrubber,
)
from rasaops_edge.privacy.face_detector import FaceDetectorError
from rasaops_shared.events import (
    EventFrameRef,
    EventMeta,
    EventPackage,
    EventType,
    RedactionStatus,
)


def _frame(h: int = 240, w: int = 320, fill: int = 40) -> np.ndarray:
    """Synthetic BGR frame with a bright rectangle used as a 'face' texture."""
    img = np.full((h, w, 3), fill, dtype=np.uint8)
    # Distinct high-frequency-ish face patch (checker) so blur changes pixels
    for y in range(40, 100):
        for x in range(100, 160):
            img[y, x] = (255, 200, 180) if ((x + y) % 2 == 0) else (20, 30, 40)
    return img


def _assert_region_changed(orig: np.ndarray, scrubbed: np.ndarray, xyxy) -> None:
    x1, y1, x2, y2 = (int(v) for v in xyxy)
    a = orig[y1:y2, x1:x2].astype(np.int16)
    b = scrubbed[y1:y2, x1:x2].astype(np.int16)
    assert a.shape == b.shape and a.size > 0
    assert not np.array_equal(a, b), "expected blur to alter face/head region pixels"


def test_entitlement_false_meta_only():
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=False),
        face_detector=MockFaceDetector(),
    )
    r = scrubber.scrub(_frame(), person_boxes=[(80, 30, 180, 200)])
    assert r.redaction_status == RedactionStatus.SKIPPED_META_ONLY
    assert r.frame_upload_allowed is False
    assert r.frame_bgr is None
    assert r.scrubbed is False
    assert r.privacy_dict()["frames_upload_entitled"] is False


def test_empty_scene_ok_crop_only():
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True),
        face_detector=MockFaceDetector(faces=[]),
    )
    frame = _frame()
    r = scrubber.scrub(frame, person_boxes=[])
    assert r.redaction_status == RedactionStatus.OK
    assert r.frame_upload_allowed is True
    assert r.scrubbed is True
    assert r.method == "crop_only"
    assert r.face_boxes_redacted == 0
    assert r.frame_bgr is not None
    assert r.frame_bgr.shape == frame.shape


def test_frontal_faces_ok_blurred():
    """Frontal face(s), conf ≥ T → ok, faces blurred."""
    face = FaceBox(xyxy=(100, 40, 160, 100), conf=0.92)
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True, face_conf_min=0.50),
        face_detector=MockFaceDetector(faces=[face]),
    )
    frame = _frame()
    person = (90, 30, 170, 210)
    r = scrubber.scrub(frame, person_boxes=[person])
    assert r.redaction_status == RedactionStatus.OK
    assert r.frame_upload_allowed is True
    assert r.method == "crop+face_blur"
    assert r.face_boxes_redacted == 1
    assert r.frame_bgr is not None
    _assert_region_changed(frame, r.frame_bgr, face.xyxy)
    # Non-face area largely unchanged (outside blur pad)
    assert np.array_equal(frame[0:10, 0:10], r.frame_bgr[0:10, 0:10])


def test_back_view_person_zero_faces_head_roi():
    """Back-view person, zero face boxes → person_no_face_heuristic."""
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True, head_roi_frac=0.30),
        face_detector=MockFaceDetector(faces=[]),
    )
    frame = _frame()
    # Tall person box so head ROI is valid
    person = (80, 20, 180, 220)
    r = scrubber.scrub(frame, person_boxes=[person])
    assert r.redaction_status == RedactionStatus.PERSON_NO_FACE_HEURISTIC
    assert r.frame_upload_allowed is True
    assert r.method == "crop+head_roi_blur"
    assert r.person_boxes_heuristic == 1
    assert r.frame_bgr is not None
    # Top 30% of person box should be blurred
    head = (80, 20, 180, 20 + 0.30 * (220 - 20))
    _assert_region_changed(frame, r.frame_bgr, head)


def test_back_view_tiny_person_box_fails():
    """Invalid / too-small person box → failed_no_frame (no heuristic upload)."""
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(
            frames_upload_entitled=True,
            min_person_side_px=8.0,
        ),
        face_detector=MockFaceDetector(faces=[]),
    )
    r = scrubber.scrub(_frame(), person_boxes=[(10, 10, 14, 14)])
    assert r.redaction_status == RedactionStatus.FAILED_NO_FRAME
    assert r.frame_upload_allowed is False
    assert r.frame_bgr is None


def test_crowd_all_faces_high_conf_blurred():
    """Crowded entrance: all faces ≥ T → ok and all blurred."""
    faces = [
        FaceBox(xyxy=(40, 30, 80, 80), conf=0.88),
        FaceBox(xyxy=(120, 40, 160, 90), conf=0.71),
        FaceBox(xyxy=(200, 35, 240, 85), conf=0.55),
    ]
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True, face_conf_min=0.50),
        face_detector=MockFaceDetector(faces=faces),
    )
    frame = _frame(h=200, w=280)
    # Textured face regions (uniform color is invariant under Gaussian blur)
    for f in faces:
        x1, y1, x2, y2 = (int(v) for v in f.xyxy)
        for y in range(y1, y2):
            for x in range(x1, x2):
                frame[y, x] = (255, 0, 0) if ((x + y) % 2 == 0) else (0, 255, 0)
    persons = [(30, 20, 90, 180), (110, 20, 170, 180), (190, 20, 250, 180)]
    r = scrubber.scrub(frame, person_boxes=persons)
    assert r.redaction_status == RedactionStatus.OK
    assert r.face_boxes_redacted == 3
    assert r.frame_upload_allowed is True
    for f in faces:
        _assert_region_changed(frame, r.frame_bgr, f.xyxy)


def test_crowd_any_face_below_threshold_meta_only():
    """Crowded: any face conf < T → failed_no_frame, meta-only."""
    faces = [
        FaceBox(xyxy=(40, 30, 80, 80), conf=0.90),
        FaceBox(xyxy=(120, 40, 160, 90), conf=0.40),  # below 0.50
    ]
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True, face_conf_min=0.50),
        face_detector=MockFaceDetector(faces=faces),
    )
    r = scrubber.scrub(_frame(), person_boxes=[(30, 20, 180, 200)])
    assert r.redaction_status == RedactionStatus.FAILED_NO_FRAME
    assert r.frame_upload_allowed is False
    assert r.frame_bgr is None
    assert r.method == "face_conf_below_threshold"


def test_detector_hard_fail_meta_only():
    """Detector hard-fail / timeout → failed_no_frame, meta-only."""
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True),
        face_detector=MockFaceDetector(raise_error=True, error_message="timeout"),
    )
    r = scrubber.scrub(_frame(), person_boxes=[(80, 30, 180, 200)])
    assert r.redaction_status == RedactionStatus.FAILED_NO_FRAME
    assert r.frame_upload_allowed is False
    assert r.frame_bgr is None
    assert r.error == "timeout"
    assert r.method == "detector_error"


def test_missing_frame_fails():
    scrubber = PrivacyScrubber(config=PrivacyConfig(frames_upload_entitled=True))
    r = scrubber.scrub(None, person_boxes=[(10, 10, 50, 50)])
    assert r.redaction_status == RedactionStatus.FAILED_NO_FRAME
    assert r.frame_upload_allowed is False


def test_privacy_dict_schema_shape():
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True),
        face_detector=MockFaceDetector(
            faces=[FaceBox(xyxy=(100, 40, 160, 100), conf=0.9)]
        ),
    )
    r = scrubber.scrub(_frame(), person_boxes=[(90, 30, 170, 210)])
    d = r.privacy_dict()
    assert set(d.keys()) >= {
        "scrubbed",
        "method",
        "redaction_status",
        "face_boxes_redacted",
        "fail_closed",
        "frames_upload_entitled",
    }
    assert d["redaction_status"] == "ok"
    assert d["scrubbed"] is True


def test_event_package_never_unredacted_on_fail():
    """Cloud-bound packages never attach unredacted frames on scrub fail."""
    meta = EventMeta(
        event_type=EventType.TABLE_OCCUPIED,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.FAILED_NO_FRAME,
    )
    frame = EventFrameRef(
        width=320,
        height=240,
        redaction_status=RedactionStatus.FAILED_NO_FRAME,
    )
    pkg = EventPackage(meta=meta, frame=frame)
    assert pkg.may_attach_frame_bytes() is False


def test_event_package_allows_heuristic_status():
    meta = EventMeta(
        event_type=EventType.CUSTOMER_ENTRY,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.PERSON_NO_FACE_HEURISTIC,
    )
    frame = EventFrameRef(
        width=320,
        height=240,
        redaction_status=RedactionStatus.PERSON_NO_FACE_HEURISTIC,
    )
    pkg = EventPackage(meta=meta, frame=frame)
    assert pkg.may_attach_frame_bytes() is True


def test_pipeline_scrub_stamps_events():
    from pathlib import Path

    from rasaops_edge.inference.mock import person
    from rasaops_edge.pipeline import EdgePipeline

    root = Path(__file__).resolve().parents[2]
    zones = root / "shared" / "schemas" / "examples" / "zones.v1.sample.json"
    face_det = MockFaceDetector(faces=[])
    scrubber = PrivacyScrubber(
        config=PrivacyConfig(frames_upload_entitled=True),
        face_detector=face_det,
    )
    pipe = EdgePipeline.from_paths(
        zones,
        scrubber=scrubber,
        frames_upload_entitled=True,
    )
    pipe.config.t_enter_sec = 0.5
    pipe.config.sample_fps = 2.0
    pipe.occupancy.config = pipe.config

    frame = _frame(h=480, w=640)
    # person in table-1
    r = None
    for i in range(4):
        r = pipe.process_synthetic(
            [person((80, 240, 140, 320))],
            timestamp_ms=50_000 + i * 500,
            frame_bgr=frame,
            scrub=True,
        )
    assert r is not None
    assert r.scrub is not None
    assert r.scrub.redaction_status == RedactionStatus.PERSON_NO_FACE_HEURISTIC
    assert r.metrics.get("frame_upload_allowed") is True
    for ev in r.events:
        assert ev.redaction_status == RedactionStatus.PERSON_NO_FACE_HEURISTIC


def test_face_detector_error_type():
    with pytest.raises(FaceDetectorError):
        raise FaceDetectorError("boom")
