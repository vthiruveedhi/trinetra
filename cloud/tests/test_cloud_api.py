from fastapi.testclient import TestClient

from cloud.api.app import create_app


def test_health_and_ingest_and_chat():
    client = TestClient(create_app())
    assert client.get("/health").json()["ok"] is True

    r = client.post(
        "/v1/events/ingest",
        json={
            "meta": {
                "event_id": "e1",
                "event_type": "table_occupied",
                "site_id": "lab-site-001",
                "device_id": "lab-device-1",
                "camera_id": "cam-primary",
                "confidence": 0.9,
                "redaction_status": "ok",
                "frames_upload_entitled": True,
            },
            "frame_b64": None,
        },
        headers={"X-Device-Id": "lab-device-1", "Authorization": "Bearer dev-token"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"

    events = client.get("/v1/events").json()["events"]
    assert any(e["event_id"] == "e1" for e in events)

    insights = client.get("/v1/insights").json()["insights"]
    assert len(insights) >= 1

    chat = client.post(
        "/v1/chat",
        json={"site_id": "lab-site-001", "question": "What happened?"},
    ).json()
    assert chat["answer"]
    assert chat["grounded"] is True


def test_reject_frame_when_not_entitled():
    client = TestClient(create_app())
    import base64

    r = client.post(
        "/v1/events/ingest",
        json={
            "meta": {
                "event_type": "table_freed",
                "site_id": "lab-site-001",
                "device_id": "lab-device-1",
                "camera_id": "cam-primary",
                "confidence": 0.8,
                "redaction_status": "ok",
                "frames_upload_entitled": False,
            },
            "frame_b64": base64.b64encode(b"not-a-real-jpeg").decode(),
        },
        headers={"X-Device-Id": "lab-device-1", "Authorization": "Bearer dev-token"},
    )
    body = r.json()
    assert body["has_frame"] is False
    assert body["frame_rejected"] is True
