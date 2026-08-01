from pathlib import Path

from rasaops_edge.queue import EventQueue, QueueStatus
from rasaops_shared.events import EventMeta, EventType, RedactionStatus


def _meta(**kw) -> EventMeta:
    base = dict(
        event_type=EventType.TABLE_OCCUPIED,
        site_id="lab-site-001",
        device_id="dev-1",
        camera_id="cam-primary",
        confidence=0.9,
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.OK,
    )
    base.update(kw)
    return EventMeta(**base)


def test_enqueue_claim_ack(tmp_path: Path):
    q = EventQueue(tmp_path / "q.sqlite", frames_dir=tmp_path / "frames")
    mid = q.enqueue(_meta(), frame_jpeg=b"\xff\xd8fake", scrub_allows_frame=True)
    assert q.depth() == 1
    batch = q.claim_batch(5)
    assert len(batch) == 1
    assert batch[0].id == mid
    assert batch[0].frame_path is not None
    assert Path(batch[0].frame_path).exists()
    q.ack(mid)
    assert q.depth() == 0
    assert q.depth(QueueStatus.ACKED) == 1
    q.close()


def test_nack_backoff_and_deadletter(tmp_path: Path):
    q = EventQueue(
        tmp_path / "q.sqlite",
        max_attempts=3,
        base_backoff_sec=0.01,
    )
    mid = q.enqueue(_meta(redaction_status=RedactionStatus.FAILED_NO_FRAME))
    for _ in range(3):
        # ensure item is pending and due
        q._conn.execute(
            "UPDATE event_queue SET status='pending', next_attempt_ms=0 WHERE id=?",
            (mid,),
        )
        q._conn.commit()
        batch = q.claim_batch(1)
        assert batch
        q.nack(mid, "fail")
    assert q.depth(QueueStatus.DEADLETTER) == 1
    q.close()


def test_no_frame_when_not_entitled(tmp_path: Path):
    q = EventQueue(tmp_path / "q.sqlite")
    q.enqueue(
        _meta(frames_upload_entitled=False, redaction_status=RedactionStatus.OK),
        frame_jpeg=b"abc",
        scrub_allows_frame=True,
    )
    item = q.claim_batch(1)[0]
    assert item.frame_path is None
    q.close()
