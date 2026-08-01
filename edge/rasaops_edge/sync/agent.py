"""HTTPS sync agent: drain SQLite queue to cloud ingest."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from rasaops_edge.queue.sqlite_queue import EventQueue


@dataclass
class SyncResult:
    attempted: int = 0
    acked: int = 0
    failed: int = 0
    offline: bool = False
    last_error: Optional[str] = None


class SyncAgent:
    """
    Drain pending queue items to cloud POST /v1/events/ingest.

    Offline: nack with backoff (items remain pending).
    """

    def __init__(
        self,
        queue: EventQueue,
        *,
        cloud_base_url: str = "http://127.0.0.1:8080",
        device_id: str = "lab-device-1",
        site_id: str = "lab-site-001",
        device_token: str = "dev-token",
        timeout_sec: float = 5.0,
        batch_size: int = 10,
    ) -> None:
        self.queue = queue
        self.cloud_base_url = cloud_base_url.rstrip("/")
        self.device_id = device_id
        self.site_id = site_id
        self.device_token = device_token
        self.timeout_sec = timeout_sec
        self.batch_size = batch_size

    def drain_once(self) -> SyncResult:
        result = SyncResult()
        items = self.queue.claim_batch(self.batch_size)
        if not items:
            return result

        for item in items:
            result.attempted += 1
            try:
                payload = self._build_payload(item)
                with httpx.Client(timeout=self.timeout_sec) as client:
                    r = client.post(
                        f"{self.cloud_base_url}/v1/events/ingest",
                        json=payload,
                        headers={
                            "X-Device-Id": self.device_id,
                            "X-Site-Id": self.site_id,
                            "Authorization": f"Bearer {self.device_token}",
                        },
                    )
                if r.status_code in (200, 201, 202):
                    self.queue.ack(item.id)
                    result.acked += 1
                else:
                    err = f"HTTP {r.status_code}: {r.text[:200]}"
                    self.queue.nack(item.id, err)
                    result.failed += 1
                    result.last_error = err
            except (httpx.HTTPError, OSError) as exc:
                self.queue.nack(item.id, str(exc))
                result.failed += 1
                result.offline = True
                result.last_error = str(exc)
        return result

    def _build_payload(self, item) -> dict:
        meta = item.event_meta()
        body: dict = {
            "schema_version": "event_package.v1",
            "meta": meta.model_dump(mode="json"),
            "frame_b64": None,
        }
        if item.frame_path and Path(item.frame_path).exists():
            if meta.frames_upload_entitled and meta.redaction_status.value in (
                "ok",
                "person_no_face_heuristic",
            ):
                raw = Path(item.frame_path).read_bytes()
                body["frame_b64"] = base64.b64encode(raw).decode("ascii")
        return body
