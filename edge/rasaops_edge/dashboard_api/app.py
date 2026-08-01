"""Local kiosk API: /local/metrics, /local/events, static UI."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from rasaops_shared.events import LocalMetricsSnapshot


@dataclass
class DashboardState:
    """Shared in-memory state between edge agent and API."""

    site_id: str = "lab-site-001"
    device_id: str = "lab-device-1"
    metrics: Dict[str, Any] = field(default_factory=dict)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    queue_depth: int = 0
    last_sync: Optional[Dict[str, Any]] = None
    online: bool = False
    lang: str = "en"
    source: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    def update_metrics(self, metrics: Dict[str, Any], queue_depth: int = 0) -> None:
        with self.lock:
            self.metrics = dict(metrics)
            self.queue_depth = queue_depth

    def push_event(self, event: Dict[str, Any]) -> None:
        with self.lock:
            self.recent_events.insert(0, event)
            self.recent_events = self.recent_events[:100]

    def snapshot(self) -> LocalMetricsSnapshot:
        with self.lock:
            m = self.metrics
            return LocalMetricsSnapshot(
                site_id=self.site_id,
                device_id=self.device_id,
                timestamp=datetime.now(timezone.utc),
                tables=list(m.get("tables") or []),
                entrance_queue_count=int(m.get("entrance_queue_count") or 0),
                pending_actions=list(m.get("pending_actions") or []),
                queue_depth=self.queue_depth,
            )


def create_app(state: Optional[DashboardState] = None) -> FastAPI:
    state = state or DashboardState()
    app = FastAPI(title="RasaOps Edge Dashboard", version="0.1.0")
    app.state.state = state

    ui_dir = Path(__file__).resolve().parent / "static"
    ui_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": "rasaops-edge-dashboard"}

    @app.get("/local/metrics")
    def local_metrics() -> dict:
        snap = state.snapshot()
        with state.lock:
            m = state.metrics or {}
            scene = m.get("scene") or {}
            seating = m.get("seating") or {
                "tables_occupied": scene.get("tables_occupied"),
                "tables_free": scene.get("tables_free"),
                "seated_people": scene.get("seated_people"),
                "seat_capacity": scene.get("seat_capacity"),
                "seats_free_est": scene.get("seats_free_est"),
                "seat_occupancy_rate": scene.get("seat_occupancy_rate"),
                "table_occupancy_rate": scene.get("table_occupancy_rate"),
            }
            return {
                **snap.model_dump(mode="json"),
                "online": state.online,
                "last_sync": state.last_sync,
                "lang": state.lang,
                "source": state.source,
                "stream_title": m.get("stream_title"),
                "scene": scene,
                "seating": seating,
                "person_count": scene.get("person_count") or m.get("person_count"),
                "bar_crowd_est": m.get("bar_crowd_est")
                or (scene.get("pub") or {}).get("bar_crowd_est"),
                "queue_zones": m.get("queue_zones") or [],
                "raw_metrics": state.metrics,
            }

    @app.get("/local/scene")
    def local_scene() -> dict:
        with state.lock:
            return {
                "scene": (state.metrics or {}).get("scene") or {},
                "source": state.source,
                "stream_title": (state.metrics or {}).get("stream_title"),
            }

    @app.get("/local/events")
    def local_events(limit: int = 50) -> dict:
        with state.lock:
            return {"events": state.recent_events[: max(1, min(limit, 200))]}

    @app.get("/local/status")
    def local_status() -> dict:
        with state.lock:
            return {
                "site_id": state.site_id,
                "device_id": state.device_id,
                "queue_depth": state.queue_depth,
                "online": state.online,
                "last_sync": state.last_sync,
            }

    @app.get("/", response_model=None)
    def index():
        index_path = ui_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return HTMLResponse("<h1>RasaOps</h1><p>UI missing</p>")

    if ui_dir.exists():
        app.mount("/static", StaticFiles(directory=str(ui_dir)), name="static")

    return app
