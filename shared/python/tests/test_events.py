from rasaops_shared.events import (
    EventMeta,
    EventPackage,
    EventType,
    EventFrameRef,
    RedactionStatus,
)


def test_meta_only_package():
    meta = EventMeta(
        event_type=EventType.TABLE_OCCUPIED,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        confidence=0.9,
        frames_upload_entitled=False,
        redaction_status=RedactionStatus.SKIPPED_META_ONLY,
    )
    pkg = EventPackage(meta=meta)
    assert pkg.may_attach_frame_bytes() is False


def test_frame_allowed_when_entitled_and_ok():
    meta = EventMeta(
        event_type=EventType.CUSTOMER_ENTRY,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        confidence=0.8,
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.OK,
    )
    frame = EventFrameRef(
        width=640,
        height=480,
        redaction_status=RedactionStatus.OK,
    )
    pkg = EventPackage(meta=meta, frame=frame)
    assert pkg.may_attach_frame_bytes() is True


def test_fail_closed_blocks_frame():
    meta = EventMeta(
        event_type=EventType.QUEUE_BUILDUP,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.FAILED_NO_FRAME,
    )
    frame = EventFrameRef(
        width=640,
        height=480,
        redaction_status=RedactionStatus.FAILED_NO_FRAME,
    )
    pkg = EventPackage(meta=meta, frame=frame)
    assert pkg.may_attach_frame_bytes() is False
