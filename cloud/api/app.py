"""
RasaOps Cloud API — L1 dogfood (PR-11/12/13/16/17 simplified).

In-memory store; no Postgres required for lab. Frames rejected if entitlement off.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from cloud.api.store import STORE
except ImportError:  # pragma: no cover
    from .store import STORE


class IngestBody(BaseModel):
    schema_version: str = "event_package.v1"
    meta: dict[str, Any]
    frame_b64: Optional[str] = None


class ChatBody(BaseModel):
    site_id: str = "lab-site-001"
    question: str
    lang: str = "en"


def create_app() -> FastAPI:
    app = FastAPI(title="RasaOps Cloud API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    frames_dir = Path(__file__).resolve().parents[2] / "data" / "cloud_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "rasaops-cloud"}

    @app.get("/v1/tenants")
    def list_tenants() -> dict:
        return {
            "tenants": [
                {
                    "tenant_id": t.tenant_id,
                    "name": t.name,
                    "frames_upload": t.frames_upload,
                }
                for t in STORE.tenants.values()
            ]
        }

    @app.post("/v1/events/ingest")
    def ingest(
        body: IngestBody,
        authorization: Optional[str] = Header(default=None),
        x_device_id: Optional[str] = Header(default=None, alias="X-Device-Id"),
        x_site_id: Optional[str] = Header(default=None, alias="X-Site-Id"),
    ) -> dict:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        device_id = x_device_id or body.meta.get("device_id") or "unknown"
        dev = STORE.auth_device(device_id, token or "dev-token")
        if not dev:
            raise HTTPException(status_code=401, detail="device auth failed")

        tenant = STORE.tenants.get(dev.tenant_id)
        frames_ok = bool(tenant.frames_upload) if tenant else True
        # also honor meta entitlement
        if body.meta.get("frames_upload_entitled") is False:
            frames_ok = False

        has_frame = bool(body.frame_b64)
        frame_rejected = False
        if has_frame and not frames_ok:
            frame_rejected = True
            has_frame = False

        redaction = body.meta.get("redaction_status")
        if has_frame and redaction not in ("ok", "person_no_face_heuristic"):
            frame_rejected = True
            has_frame = False

        if has_frame and body.frame_b64:
            try:
                raw = base64.b64decode(body.frame_b64)
                eid = body.meta.get("event_id") or "frame"
                (frames_dir / f"{eid}.jpg").write_bytes(raw[:400_000])
            except Exception:
                frame_rejected = True
                has_frame = False

        # force site/device alignment
        body.meta.setdefault("device_id", device_id)
        body.meta.setdefault("site_id", x_site_id or dev.site_id)

        eid = STORE.ingest_event(body.meta, has_frame=has_frame, frame_rejected=frame_rejected)
        return {
            "status": "accepted",
            "event_id": eid,
            "has_frame": has_frame,
            "frame_rejected": frame_rejected,
        }

    @app.get("/v1/events")
    def list_events(site_id: Optional[str] = None, limit: int = 50) -> dict:
        with STORE.lock:
            evs = list(STORE.events)
        if site_id:
            evs = [e for e in evs if e["meta"].get("site_id") == site_id]
        return {"events": evs[: max(1, min(limit, 200))]}

    @app.get("/v1/insights")
    def list_insights(site_id: Optional[str] = None, limit: int = 20) -> dict:
        with STORE.lock:
            ins = list(STORE.insights)
        if site_id:
            ins = [i for i in ins if i.get("site_id") == site_id]
        return {"insights": ins[: max(1, min(limit, 100))]}

    @app.post("/v1/chat")
    def chat(body: ChatBody) -> dict:
        return STORE.chat(body.site_id, body.question, body.lang)

    return app


app = create_app()
