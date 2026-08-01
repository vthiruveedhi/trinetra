"""In-memory multi-tenant store for L1 dogfood (swap for Postgres in production)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class Tenant:
    tenant_id: str
    name: str
    frames_upload: bool = True


@dataclass
class Device:
    device_id: str
    site_id: str
    tenant_id: str
    token: str = "dev-token"


@dataclass
class CloudStore:
    tenants: Dict[str, Tenant] = field(default_factory=dict)
    devices: Dict[str, Device] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    insights: List[Dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed_lab(self) -> None:
        if "lab-tenant" in self.tenants:
            return
        self.tenants["lab-tenant"] = Tenant(
            tenant_id="lab-tenant", name="Lab Restaurant", frames_upload=True
        )
        self.devices["lab-device-1"] = Device(
            device_id="lab-device-1",
            site_id="lab-site-001",
            tenant_id="lab-tenant",
            token="dev-token",
        )
        self.devices["lab-pc-webcam"] = Device(
            device_id="lab-pc-webcam",
            site_id="lab-site-001",
            tenant_id="lab-tenant",
            token="dev-token",
        )
        self.devices["lab-cam-0"] = Device(
            device_id="lab-cam-0",
            site_id="lab-site-001",
            tenant_id="lab-tenant",
            token="dev-token",
        )

    def auth_device(self, device_id: str, token: str) -> Optional[Device]:
        dev = self.devices.get(device_id)
        if not dev:
            # auto-register lab devices for dogfood
            dev = Device(
                device_id=device_id,
                site_id="lab-site-001",
                tenant_id="lab-tenant",
                token=token or "dev-token",
            )
            self.devices[device_id] = dev
        if token and dev.token != token and token != "dev-token":
            return None
        return dev

    def ingest_event(self, meta: dict, has_frame: bool, frame_rejected: bool = False) -> str:
        eid = meta.get("event_id") or str(uuid4())
        rec = {
            "event_id": eid,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta,
            "has_frame": has_frame and not frame_rejected,
            "frame_rejected": frame_rejected,
        }
        with self.lock:
            self.events.insert(0, rec)
            self.events = self.events[:5000]
            insight = self._rules_insight(meta)
            if insight:
                self.insights.insert(0, insight)
                self.insights = self.insights[:500]
        return eid

    def _rules_insight(self, meta: dict) -> Optional[dict]:
        et = meta.get("event_type")
        zone = (meta.get("zone") or {}).get("label") or (meta.get("zone") or {}).get("zone_id")
        if et == "table_occupied":
            text = f"Table {zone or '?'} is now occupied."
        elif et == "table_freed":
            text = f"Table {zone or '?'} is free — good turn opportunity."
        elif et == "queue_buildup":
            text = f"Queue buildup at {zone or 'entrance'} — staff attention recommended."
        elif et == "customer_entry":
            text = f"Customer entry detected at {zone or 'entrance'}."
        else:
            return None
        return {
            "insight_id": str(uuid4()),
            "site_id": meta.get("site_id"),
            "event_id": meta.get("event_id"),
            "text": text,
            "source": "rules_only",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "citations": [meta.get("event_id")],
        }

    def chat(self, site_id: str, question: str, lang: str = "en") -> dict:
        with self.lock:
            site_events = [e for e in self.events if e["meta"].get("site_id") == site_id][:50]
            site_insights = [i for i in self.insights if i.get("site_id") == site_id][:20]

        q = question.lower()
        citations: List[str] = []
        answer = None

        if any(k in q for k in ("free", "खाली", "available")):
            # infer from latest table events
            last_state: Dict[str, str] = {}
            for e in reversed(site_events):
                m = e["meta"]
                z = (m.get("zone") or {}).get("zone_id")
                if m.get("event_type") == "table_occupied" and z:
                    last_state[z] = "occupied"
                    citations.append(m.get("event_id"))
                if m.get("event_type") == "table_freed" and z:
                    last_state[z] = "free"
                    citations.append(m.get("event_id"))
            free = [z for z, s in last_state.items() if s == "free"]
            if free:
                answer = f"Recently freed / free tables: {', '.join(free)}."
            else:
                answer = "No free-table evidence in recent events. Check local kiosk metrics."
        elif any(k in q for k in ("queue", "कतार", "wait")):
            qb = [e for e in site_events if e["meta"].get("event_type") == "queue_buildup"]
            if qb:
                answer = f"Queue buildup events seen: {len(qb)} recently."
                citations = [qb[0]["meta"].get("event_id")]
            else:
                answer = "No queue_buildup events in recent cloud history."
        elif site_insights:
            answer = site_insights[0]["text"]
            citations = site_insights[0].get("citations") or []
        else:
            answer = (
                "I don't have enough grounded events yet. "
                "Run the edge agent so events sync, then ask about free tables or queue."
            )

        if not site_events and not site_insights:
            answer = "No context for this site yet — refuse to invent floor state."

        return {
            "answer": answer,
            "citations": [c for c in citations if c][:5],
            "lang": lang,
            "grounded": bool(site_events or site_insights),
        }


STORE = CloudStore()
STORE.seed_lab()
