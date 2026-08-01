from pathlib import Path

from fastapi.testclient import TestClient

from rasaops_edge.dashboard_api import DashboardState, create_app
from rasaops_edge.queue import EventQueue
from rasaops_edge.sync import SyncAgent
from rasaops_shared.events import EventMeta, EventType, RedactionStatus


def test_dashboard_metrics():
    state = DashboardState(site_id="s1", device_id="d1")
    state.update_metrics(
        {
            "tables": [{"zone_id": "table-1", "label": "T1", "state": "occupied", "person_count_est": 1}],
            "entrance_queue_count": 2,
        },
        queue_depth=3,
    )
    app = create_app(state)
    client = TestClient(app)
    r = client.get("/local/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["site_id"] == "s1"
    assert body["queue_depth"] == 3
    assert body["tables"][0]["state"] == "occupied"
    assert client.get("/health").json()["ok"] is True
    assert client.get("/").status_code == 200


def test_sync_to_cloud(tmp_path: Path):
    from cloud.api.app import create_app as create_cloud

    cloud = TestClient(create_cloud())
    q = EventQueue(tmp_path / "q.sqlite")
    meta = EventMeta(
        event_type=EventType.TABLE_OCCUPIED,
        site_id="lab-site-001",
        device_id="lab-device-1",
        camera_id="cam-primary",
        confidence=0.9,
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.PERSON_NO_FACE_HEURISTIC,
    )
    q.enqueue(meta)
    items = q.claim_batch(1)
    assert items
    item = items[0]
    payload = {
        "schema_version": "event_package.v1",
        "meta": item.event_meta().model_dump(mode="json"),
        "frame_b64": None,
    }
    r = cloud.post(
        "/v1/events/ingest",
        json=payload,
        headers={
            "X-Device-Id": "lab-device-1",
            "Authorization": "Bearer dev-token",
        },
    )
    assert r.status_code == 200
    q.ack(item.id)

    ev = cloud.get("/v1/events?site_id=lab-site-001").json()
    assert len(ev["events"]) >= 1
    chat = cloud.post(
        "/v1/chat",
        json={"site_id": "lab-site-001", "question": "any free tables?"},
    ).json()
    assert "answer" in chat
