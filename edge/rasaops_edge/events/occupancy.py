"""Occupancy / entrance / queue state machines (PR-05 MVP)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from rasaops_edge.tracking.byte_track import Track
from rasaops_edge.tracking.geometry import feet_in_polygon, centroid_in_polygon
from rasaops_shared.events import EventMeta, EventType, ZoneRef
from rasaops_shared.zones import Zone, ZonesDocument


class TableOccupancy(str, Enum):
    FREE = "free"
    OCCUPIED = "occupied"


@dataclass
class OccupancyConfig:
    t_enter_sec: float = 1.5
    t_leave_sec: float = 6.0
    queue_min_count: int = 3
    queue_min_duration_sec: float = 12.0
    sample_fps: float = 2.0  # for dwell sample counting
    site_id: str = "lab-site-001"
    device_id: str = "lab-device-1"
    camera_id: str = "cam-primary"
    model_version: str = "unknown"
    frames_upload_entitled: bool = False
    # feet = bottom of person box (better for seated); centroid = box center
    zone_assoc: str = "feet"


@dataclass
class TableState:
    zone: Zone
    occupancy: TableOccupancy = TableOccupancy.FREE
    person_count: int = 0
    occupied_since_ms: Optional[int] = None
    free_since_ms: Optional[int] = None
    _enter_streak_ms: float = 0.0
    _leave_streak_ms: float = 0.0


@dataclass
class OccupancyEngine:
    zones: ZonesDocument
    config: OccupancyConfig = field(default_factory=OccupancyConfig)

    def __post_init__(self) -> None:
        self._tables: Dict[str, TableState] = {
            z.zone_id: TableState(zone=z)
            for z in self.zones.zones
            if z.kind == "table"
        }
        self._entrances = [z for z in self.zones.zones if z.kind == "entrance"]
        self._queues = [z for z in self.zones.zones if z.kind == "queue"]
        self._passes = [z for z in self.zones.zones if z.kind == "pass"]
        # fallback: treat entrance as queue zone if no dedicated queue
        if not self._queues:
            self._queues = list(self._entrances)
        self._queue_hot_ms: float = 0.0
        self._queue_alert_active: bool = False
        self._entrance_prev_count: int = 0
        self._last_ts_ms: Optional[int] = None

    @property
    def tables(self) -> Dict[str, TableState]:
        return self._tables

    def update(self, tracks: List[Track], timestamp_ms: int) -> List[EventMeta]:
        """
        Update state machines. Returns edge events (meta only).

        Privacy: track_id is NOT set on cloud-bound EventMeta (KD-23 / architecture).
        """
        if self._last_ts_ms is None:
            dt_s = 1.0 / max(self.config.sample_fps, 0.1)
        else:
            dt_s = max(0.0, (timestamp_ms - self._last_ts_ms) / 1000.0)
            if dt_s == 0:
                dt_s = 1.0 / max(self.config.sample_fps, 0.1)
        self._last_ts_ms = timestamp_ms

        events: List[EventMeta] = []
        events.extend(self._update_tables(tracks, timestamp_ms, dt_s))
        events.extend(self._update_entrance(tracks, timestamp_ms))
        events.extend(self._update_queue(tracks, timestamp_ms, dt_s))
        return events

    def snapshot_metrics(self) -> Dict[str, Any]:
        return {
            "tables": [
                {
                    "zone_id": st.zone.zone_id,
                    "label": st.zone.label,
                    "state": st.occupancy.value,
                    "person_count_est": st.person_count,
                }
                for st in self._tables.values()
            ],
            "entrance_queue_count": self._count_in_zones(self._queues, []),  # filled by caller prefer
            "queue_alert_active": self._queue_alert_active,
        }

    def metrics_with_tracks(self, tracks: List[Track]) -> Dict[str, Any]:
        assigned = self._assign_people(tracks)
        from collections import Counter

        counts = Counter(assigned.values())
        tables = []
        seated = 0
        capacity = 0
        for st in self._tables.values():
            n = int(counts.get(st.zone.zone_id, 0))
            cap = int(st.zone.capacity_hint or 4)
            capacity += cap
            seated += n
            tables.append(
                {
                    "zone_id": st.zone.zone_id,
                    "label": st.zone.label,
                    "state": st.occupancy.value,
                    "person_count_est": n,
                    "capacity_hint": cap,
                    "fill_rate": round(min(1.0, n / max(1, cap)), 2),
                }
            )
        queue_zones = [
            {
                "zone_id": z.zone_id,
                "label": z.label,
                "kind": "queue",
                "person_count_est": int(counts.get(z.zone_id, 0)),
                "capacity_hint": int(z.capacity_hint or 0) or None,
            }
            for z in self._queues
        ]
        bar_people = sum(
            int(q["person_count_est"] or 0)
            for q in queue_zones
            if "bar" in ((q.get("zone_id") or "") + " " + (q.get("label") or "")).lower()
        )
        if bar_people == 0:
            # fallback: any queue labeled rail / counter
            bar_people = sum(
                int(q["person_count_est"] or 0)
                for q in queue_zones
                if any(
                    k in ((q.get("zone_id") or "") + " " + (q.get("label") or "")).lower()
                    for k in ("rail", "counter", "bar")
                )
            )
        occ_tables = sum(1 for t in tables if t["state"] == "occupied")
        free_tables = sum(1 for t in tables if t["state"] == "free")
        snap = {
            "tables": tables,
            "queue_zones": queue_zones,
            "pass_stations": [
                {
                    "zone_id": z.zone_id,
                    "label": z.label,
                    "kind": z.kind,
                    "person_count_est": int(counts.get(z.zone_id, 0)),
                }
                for z in self._passes
            ],
            "pass_staff_count": sum(int(counts.get(z.zone_id, 0)) for z in self._passes),
            "entrance_queue_count": sum(int(counts.get(z.zone_id, 0)) for z in self._queues),
            "bar_crowd_est": bar_people,
            "queue_alert_active": self._queue_alert_active,
            "seating": {
                "tables_total": len(tables),
                "tables_occupied": occ_tables,
                "tables_free": free_tables,
                "seated_people": seated,
                "seat_capacity": capacity,
                "seats_free_est": max(0, capacity - seated),
                "table_occupancy_rate": round(occ_tables / max(1, len(tables)), 3),
                "seat_occupancy_rate": round(seated / max(1, capacity), 3),
            },
        }
        return snap

    def _update_tables(
        self, tracks: List[Track], timestamp_ms: int, dt_s: float
    ) -> List[EventMeta]:
        out: List[EventMeta] = []
        assigned = self._assign_people(tracks)
        from collections import Counter

        counts = Counter(assigned.values())
        for st in self._tables.values():
            count = int(counts.get(st.zone.zone_id, 0))
            st.person_count = count

            if count >= 1:
                st._enter_streak_ms += dt_s * 1000.0
                st._leave_streak_ms = 0.0
            else:
                st._leave_streak_ms += dt_s * 1000.0
                st._enter_streak_ms = 0.0

            if (
                st.occupancy == TableOccupancy.FREE
                and st._enter_streak_ms >= self.config.t_enter_sec * 1000.0
            ):
                st.occupancy = TableOccupancy.OCCUPIED
                st.occupied_since_ms = timestamp_ms
                st.free_since_ms = None
                out.append(
                    self._event(
                        EventType.TABLE_OCCUPIED,
                        st.zone,
                        timestamp_ms,
                        confidence=0.9,
                        metrics={
                            "person_count_est": count,
                            "t_enter_sec": self.config.t_enter_sec,
                        },
                    )
                )
            elif (
                st.occupancy == TableOccupancy.OCCUPIED
                and st._leave_streak_ms >= self.config.t_leave_sec * 1000.0
            ):
                st.occupancy = TableOccupancy.FREE
                st.free_since_ms = timestamp_ms
                st.occupied_since_ms = None
                out.append(
                    self._event(
                        EventType.TABLE_FREED,
                        st.zone,
                        timestamp_ms,
                        confidence=0.9,
                        metrics={
                            "person_count_est": 0,
                            "t_leave_sec": self.config.t_leave_sec,
                        },
                    )
                )
        return out

    def _update_entrance(self, tracks: List[Track], timestamp_ms: int) -> List[EventMeta]:
        if not self._entrances:
            return []
        count = sum(self._count_tracks_in_zone(tracks, z) for z in self._entrances)
        out: List[EventMeta] = []
        # Rising edge: someone newly in entrance zone
        if count > self._entrance_prev_count:
            z = self._entrances[0]
            out.append(
                self._event(
                    EventType.CUSTOMER_ENTRY,
                    z,
                    timestamp_ms,
                    confidence=0.75,
                    metrics={
                        "entrance_count": count,
                        "delta": count - self._entrance_prev_count,
                    },
                )
            )
        self._entrance_prev_count = count
        return out

    def _update_queue(
        self, tracks: List[Track], timestamp_ms: int, dt_s: float
    ) -> List[EventMeta]:
        if not self._queues:
            return []
        count = sum(self._count_tracks_in_zone(tracks, z) for z in self._queues)
        out: List[EventMeta] = []
        if count >= self.config.queue_min_count:
            self._queue_hot_ms += dt_s * 1000.0
        else:
            self._queue_hot_ms = 0.0
            self._queue_alert_active = False

        if (
            not self._queue_alert_active
            and self._queue_hot_ms >= self.config.queue_min_duration_sec * 1000.0
        ):
            self._queue_alert_active = True
            z = self._queues[0]
            out.append(
                self._event(
                    EventType.QUEUE_BUILDUP,
                    z,
                    timestamp_ms,
                    confidence=0.8,
                    metrics={
                        "queue_count": count,
                        "min_count": self.config.queue_min_count,
                        "min_duration_sec": self.config.queue_min_duration_sec,
                    },
                )
            )
        return out

    def _track_in_zone(self, tr: Track, zone: Zone) -> bool:
        use_feet = (self.config.zone_assoc or "feet").lower() != "centroid"
        if use_feet:
            hit = feet_in_polygon(tr.xyxy, zone.polygon)
            if not hit:
                hit = centroid_in_polygon(tr.xyxy, zone.polygon)
            return hit
        return centroid_in_polygon(tr.xyxy, zone.polygon)

    def _count_tracks_in_zone(self, tracks: List[Track], zone: Zone) -> int:
        """Exclusive count: each person assigned to at most one zone (tables first)."""
        # Prefer calling _assign_people once via metrics path; keep simple scan for single zone
        assigned = self._assign_people(tracks)
        return sum(1 for zid in assigned.values() if zid == zone.zone_id)

    def _assign_people(self, tracks: List[Track]) -> Dict[int, str]:
        """
        Map track_id -> zone_id (exclusive).
        Priority: table zones (smallest area first for specificity), then pass, then queue/entrance.
        """
        persons = [tr for tr in tracks if (tr.class_name or "person") == "person"]
        table_zones = list(self._tables.values())
        # sort tables by polygon area ascending so nested/specific win
        def _area(z: Zone) -> float:
            xs = [p[0] for p in z.polygon]
            ys = [p[1] for p in z.polygon]
            return max(1.0, (max(xs) - min(xs)) * (max(ys) - min(ys)))

        ordered_tables = sorted([st.zone for st in table_zones], key=_area)
        ordered_rest = sorted(self._passes, key=_area) + sorted(
            self._queues + self._entrances, key=_area
        )
        ordered = ordered_tables + ordered_rest
        out: Dict[int, str] = {}
        for tr in persons:
            for z in ordered:
                if self._track_in_zone(tr, z):
                    out[tr.track_id] = z.zone_id
                    break
        return out

    def _count_in_zones(self, zones: List[Zone], tracks: List[Track]) -> int:
        return sum(self._count_tracks_in_zone(tracks, z) for z in zones)

    def _event(
        self,
        event_type: EventType,
        zone: Zone,
        timestamp_ms: int,
        confidence: float,
        metrics: Dict[str, Any],
    ) -> EventMeta:
        return EventMeta(
            event_type=event_type,
            site_id=self.config.site_id or self.zones.site_id,
            device_id=self.config.device_id,
            camera_id=self.config.camera_id or (self.zones.camera_id or "cam-primary"),
            timestamp=datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc),
            zone=ZoneRef(zone_id=zone.zone_id, kind=zone.kind, label=zone.label),
            track_id=None,  # never ship anonymous track IDs to cloud
            confidence=confidence,
            metrics=metrics,
            model_version=self.config.model_version,
            frames_upload_entitled=self.config.frames_upload_entitled,
        )
