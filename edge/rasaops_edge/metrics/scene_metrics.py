"""
Scene-aware ops metrics for live venues (dining floor, pub/bar, kitchen).

Starts from occupancy + person density and evolves thresholds every improve cycle.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from rasaops_edge.inference.backend import Detection
from rasaops_edge.metrics.atmosphere import AtmosphereEngine
from rasaops_edge.tracking.byte_track import Track

SCENE_TITLES = {
    "dining_restaurant": "Dining floor / restaurant",
    "pub_bar": "Pub / bar floor",
    "kitchen_line": "Kitchen / grill line",
}


def config_for_scene(scene_type: str, **kwargs: Any) -> "SceneConfig":
    """Presets for different venue types."""
    base = {
        "dining_restaurant": dict(
            scene_type="dining_restaurant",
            busy_person_threshold=4,
            very_busy_person_threshold=8,
            sparse_person_threshold=1,
        ),
        "pub_bar": dict(
            scene_type="pub_bar",
            busy_person_threshold=4,
            very_busy_person_threshold=8,
            sparse_person_threshold=1,
            floor_coverage_busy=0.12,
        ),
        "kitchen_line": dict(
            scene_type="kitchen_line",
            busy_person_threshold=2,
            very_busy_person_threshold=4,
            sparse_person_threshold=0,
        ),
    }.get(scene_type, {"scene_type": scene_type})
    base.update(kwargs)
    return SceneConfig(**base)


@dataclass
class SceneConfig:
    """Tunable thresholds — improved every cycle from observed stats."""

    scene_type: str = "dining_restaurant"
    busy_person_threshold: int = 4
    very_busy_person_threshold: int = 8
    sparse_person_threshold: int = 1
    dwell_busy_sec: float = 30.0
    floor_coverage_busy: float = 0.12  # fraction of frame area covered by person boxes
    improve_interval_sec: float = 20 * 60  # 20 minutes
    version: int = 1
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scene_type": self.scene_type,
            "busy_person_threshold": self.busy_person_threshold,
            "very_busy_person_threshold": self.very_busy_person_threshold,
            "sparse_person_threshold": self.sparse_person_threshold,
            "dwell_busy_sec": self.dwell_busy_sec,
            "floor_coverage_busy": self.floor_coverage_busy,
            "improve_interval_sec": self.improve_interval_sec,
            "version": self.version,
            "notes": list(self.notes)[-10:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SceneConfig":
        c = cls()
        for k, v in d.items():
            if hasattr(c, k) and k != "notes":
                setattr(c, k, v)
        c.notes = list(d.get("notes") or [])
        return c


@dataclass
class SceneMetricsEngine:
    """
    Rolling scene metrics for a restaurant dining floor live stream.
    """

    config: SceneConfig = field(default_factory=SceneConfig)
    history_max: int = 600  # ~10 min at 1 Hz
    state_path: Optional[Path] = None

    _person_hist: Deque[int] = field(default_factory=lambda: deque(maxlen=600))
    _coverage_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=600))
    _infer_ms_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    _last_improve_ts: float = field(default_factory=time.time)
    _peak_persons: int = 0
    _busy_since_ms: Optional[int] = None
    _frames: int = 0
    atmosphere: AtmosphereEngine = field(default_factory=AtmosphereEngine)

    def __post_init__(self) -> None:
        if self.state_path and self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.config = SceneConfig.from_dict(data.get("config") or {})
                self._peak_persons = int(data.get("peak_persons") or 0)
                self._last_improve_ts = float(data.get("last_improve_ts") or time.time())
            except Exception:
                pass

    def update(
        self,
        *,
        tracks: List[Track],
        detections: List[Detection],
        frame_shape: tuple[int, int],
        timestamp_ms: int,
        inference_ms: float = 0.0,
        table_metrics: Optional[List[dict]] = None,
        frame_bgr: Optional[Any] = None,
        pass_stations: Optional[List[dict]] = None,
        pass_staff_count: int = 0,
        bar_crowd_est: int = 0,
        seating: Optional[Dict[str, Any]] = None,
        queue_zones: Optional[List[dict]] = None,
        detection_count: int = 0,
    ) -> Dict[str, Any]:
        self._frames += 1
        h, w = frame_shape[:2]
        area = max(1.0, float(h * w))
        persons = [t for t in tracks if (t.class_name or "person") == "person"]
        # Prefer track count; if person detections are higher (tracker lag) use max
        n_tracks = len(persons)
        n_det = int(detection_count or 0)
        n = max(n_tracks, n_det)
        # Cap wild detection spikes (false positives)
        if n_det > n_tracks + 3:
            n = n_tracks + 2
        self._person_hist.append(n)
        self._peak_persons = max(self._peak_persons, n)
        self._infer_ms_hist.append(float(inference_ms))

        box_area = 0.0
        for t in persons:
            x1, y1, x2, y2 = t.xyxy
            box_area += max(0.0, x2 - x1) * max(0.0, y2 - y1)
        coverage = min(1.0, box_area / area)
        self._coverage_hist.append(coverage)

        cfg = self.config
        if n >= cfg.very_busy_person_threshold:
            load = "very_busy"
        elif n >= cfg.busy_person_threshold:
            load = "busy"
        elif n <= cfg.sparse_person_threshold:
            load = "sparse"
        else:
            load = "moderate"

        if load in ("busy", "very_busy"):
            if self._busy_since_ms is None:
                self._busy_since_ms = timestamp_ms
        else:
            self._busy_since_ms = None

        busy_dwell_s = 0.0
        if self._busy_since_ms is not None:
            busy_dwell_s = max(0.0, (timestamp_ms - self._busy_since_ms) / 1000.0)

        tables = table_metrics or []
        seat = seating or {}
        occ = int(seat.get("tables_occupied") or sum(1 for t in tables if t.get("state") == "occupied"))
        free = int(seat.get("tables_free") or sum(1 for t in tables if t.get("state") == "free"))
        total_tables = max(1, int(seat.get("tables_total") or len(tables) or 1))
        occupancy_rate = float(
            seat.get("table_occupancy_rate")
            if seat.get("table_occupancy_rate") is not None
            else (occ / total_tables)
        )
        seated_people = int(
            seat.get("seated_people")
            if seat.get("seated_people") is not None
            else sum(int(t.get("person_count_est") or 0) for t in tables)
        )
        seat_capacity = int(seat.get("seat_capacity") or sum(int(t.get("capacity_hint") or 4) for t in tables))
        seats_free = int(seat.get("seats_free_est") if seat.get("seats_free_est") is not None else max(0, seat_capacity - seated_people))

        # Horizontal density: left / center / right thirds
        thirds = [0, 0, 0]
        for t in persons:
            cx = (t.xyxy[0] + t.xyxy[2]) / 2.0
            idx = 0 if cx < w / 3 else (2 if cx > 2 * w / 3 else 1)
            thirds[idx] += 1

        avg_persons = (
            sum(self._person_hist) / len(self._person_hist) if self._person_hist else 0.0
        )
        avg_cov = (
            sum(self._coverage_hist) / len(self._coverage_hist) if self._coverage_hist else 0.0
        )
        p95_infer = 0.0
        if self._infer_ms_hist:
            s = sorted(self._infer_ms_hist)
            p95_infer = s[int(0.95 * (len(s) - 1))]

        improved = self.maybe_improve()

        out: Dict[str, Any] = {
            "scene_type": cfg.scene_type,
            "scene_title": SCENE_TITLES.get(cfg.scene_type, cfg.scene_type),
            "person_count": n,
            "person_count_avg": round(avg_persons, 2),
            "person_count_peak": self._peak_persons,
            "floor_load": load,
            "floor_coverage": round(coverage, 4),
            "floor_coverage_avg": round(avg_cov, 4),
            "busy_dwell_sec": round(busy_dwell_s, 1),
            "zone_density": {
                "left": thirds[0],
                "center": thirds[1],
                "right": thirds[2],
            },
            "tables_occupied": occ,
            "tables_free": free,
            "table_occupancy_rate": round(occupancy_rate, 3),
            "seated_people": seated_people,
            "seat_capacity": seat_capacity,
            "seats_free_est": seats_free,
            "seat_occupancy_rate": round(
                float(seat.get("seat_occupancy_rate") or (seated_people / max(1, seat_capacity))),
                3,
            ),
            "person_count_tracked": n_tracks,
            "person_count_detected": int(detection_count or n_tracks),
            "person_count_note": (
                "Model detections in frame — not a certified headcount. "
                "Seated/occluded/far guests are often missed at 320px; "
                "host lab uses imgsz 640 when ultralytics is enabled."
            ),
            "staffing_hint": self._staffing_hint(load, n, occupancy_rate),
            "inference_ms_p95": round(p95_infer, 1),
            "frames_processed": self._frames,
            "thresholds": {
                "busy": cfg.busy_person_threshold,
                "very_busy": cfg.very_busy_person_threshold,
                "sparse": cfg.sparse_person_threshold,
            },
            "metric_version": cfg.version,
            "last_improve_note": (cfg.notes[-1] if cfg.notes else None),
            "improved_this_tick": improved,
            "next_improve_in_sec": max(
                0, int(cfg.improve_interval_sec - (time.time() - self._last_improve_ts))
            ),
        }
        # Atmosphere: happiness / movement / drinks / eating proxies (no FR)
        atmo = self.atmosphere.update(
            frame_bgr=frame_bgr,
            tracks=tracks,
            detections=detections,
            timestamp_ms=timestamp_ms,
            table_metrics=tables,
        )
        out["vibe"] = atmo
        out["happiness_index"] = atmo["happiness_index"]
        out["atmosphere_label"] = atmo["atmosphere"]["label"]
        out["movement_label"] = atmo["movement"]["movement_label"]
        out["drinks_visible"] = atmo["drinks"]["visible_now"]
        out["eating_label"] = atmo["eating"]["label"]

        # Pub / bar extras
        if cfg.scene_type == "pub_bar":
            bar_people = int(bar_crowd_est or 0)
            standing_proxy = thirds[1]  # center often standing room
            # Also count people in queue_zones labeled bar / rail
            for q in queue_zones or []:
                zid = ((q.get("zone_id") or "") + " " + (q.get("label") or "")).lower()
                if any(k in zid for k in ("bar", "rail", "counter")):
                    bar_people = max(bar_people, int(q.get("person_count_est") or 0))
            if table_metrics:
                for t in table_metrics:
                    zid = (t.get("zone_id") or "") + " " + (t.get("label") or "")
                    if "bar" in zid.lower():
                        bar_people = max(bar_people, int(t.get("person_count_est") or 0))
            drinks_n = int(atmo["drinks"]["visible_now"])
            # Proxy: occupied seating often implies drinkware even when YOLO misses cups
            drinks_proxy = drinks_n
            if drinks_n == 0 and seated_people >= 2:
                drinks_proxy = max(1, seated_people // 2)
            out["pub"] = {
                "bar_crowd_est": bar_people,
                "standing_center_est": standing_proxy,
                "seated_people": seated_people,
                "seat_capacity": seat_capacity,
                "seats_free_est": seats_free,
                "tables_occupied": occ,
                "tables_free": free,
                "social_energy": (
                    "high"
                    if load in ("busy", "very_busy") or atmo["happiness_index"] >= 65
                    else ("low" if load == "sparse" else "medium")
                ),
                "bar_pressure": (
                    "rush"
                    if bar_people >= max(3, cfg.busy_person_threshold - 1)
                    else ("steady" if bar_people >= 1 else "quiet")
                ),
                "service_hint": self._pub_service_hint(load, bar_people, n),
                "happiness_index": atmo["happiness_index"],
                "atmosphere": atmo["atmosphere"]["label"],
                "drinks_visible": drinks_n,
                "drinks_est": drinks_proxy,
                "drinks_note": (
                    "YOLO drinkware count"
                    if drinks_n > 0
                    else (
                        "No cups/bottles detected at 320px — est from seated guests"
                        if drinks_proxy > 0
                        else "No drinkware detected (model limit on small objects)"
                    )
                ),
                "eating": atmo["eating"]["label"],
                "movement": atmo["movement"]["movement_label"],
            }
            out["drinks_visible"] = drinks_n
            out["drinks_est"] = drinks_proxy

        # Kitchen: cooking + plating line metrics
        if cfg.scene_type == "kitchen_line":
            out["kitchen"] = self._kitchen_metrics(
                load=load,
                n=n,
                table_metrics=tables,
                pass_stations=pass_stations or [],
                pass_staff_count=int(pass_staff_count or 0),
                atmo=atmo,
                thirds=thirds,
                coverage=coverage,
            )
        return out

    def _kitchen_metrics(
        self,
        *,
        load: str,
        n: int,
        table_metrics: List[dict],
        pass_stations: List[dict],
        pass_staff_count: int,
        atmo: Dict[str, Any],
        thirds: List[int],
        coverage: float,
    ) -> Dict[str, Any]:
        """Cooking + plating proxies from station occupancy, motion, and food objects."""
        cook_keys = ("cook", "grill", "burner", "prep", "line", "station")
        plate_keys = ("plat", "pass", "expo", "garnish", "window")

        cook_staff = 0
        cook_active_stations = 0
        plating_from_tables = 0
        station_detail: List[dict] = []
        for t in table_metrics:
            zid = ((t.get("zone_id") or "") + " " + (t.get("label") or "")).lower()
            cnt = int(t.get("person_count_est") or 0)
            state = (t.get("state") or "free").lower()
            is_cook = any(k in zid for k in cook_keys)
            is_plate = any(k in zid for k in plate_keys)
            if is_cook:
                cook_staff += cnt
                if state == "occupied" or cnt >= 1:
                    cook_active_stations += 1
            if is_plate:
                plating_from_tables += cnt
            station_detail.append(
                {
                    "zone_id": t.get("zone_id"),
                    "label": t.get("label"),
                    "role": "cook" if is_cook else ("plating" if is_plate else "other"),
                    "person_count_est": cnt,
                    "state": state,
                }
            )

        pass_people = pass_staff_count
        if pass_people == 0 and pass_stations:
            pass_people = sum(int(p.get("person_count_est") or 0) for p in pass_stations)
        plating_staff = max(pass_people, plating_from_tables)
        for p in pass_stations:
            station_detail.append(
                {
                    "zone_id": p.get("zone_id"),
                    "label": p.get("label"),
                    "role": "plating",
                    "person_count_est": int(p.get("person_count_est") or 0),
                    "state": "active" if int(p.get("person_count_est") or 0) >= 1 else "idle",
                }
            )

        # Fallback for unzoned chef-POV: center/bottom density ≈ cook, right ≈ pass
        if cook_staff == 0 and n > 0 and not any(
            d["role"] == "cook" and d["person_count_est"] for d in station_detail
        ):
            cook_staff = max(cook_staff, thirds[0] + thirds[1])
        if plating_staff == 0 and n > 0:
            plating_staff = max(plating_staff, thirds[2])

        motion = (atmo.get("movement") or {})
        motion_label = motion.get("movement_label") or "low"
        mean_speed = float(motion.get("mean_person_speed_px_s") or 0.0)
        frame_motion = float(motion.get("frame_motion_energy") or 0.0)
        food_visible = int((atmo.get("eating") or {}).get("food_objects_visible") or 0)

        # Cooking load: staff at cook stations + motion (hands/fire work)
        if cook_staff >= 2 or (cook_staff >= 1 and motion_label == "high"):
            cooking_load = "rush"
        elif cook_staff >= 1 or cook_active_stations >= 1:
            cooking_load = "active"
        elif n >= 1 and frame_motion > 0.02:
            cooking_load = "light"
        else:
            cooking_load = "idle"

        # Plating pressure: pass staff + food objects (dishes leaving line)
        if plating_staff >= 2 or (plating_staff >= 1 and food_visible >= 2):
            plating_pressure = "backed_up"
        elif plating_staff >= 1 or food_visible >= 1:
            plating_pressure = "plating"
        elif cook_staff >= 1 and cooking_load in ("active", "rush"):
            plating_pressure = "cook_to_pass"
        else:
            plating_pressure = "clear"

        # Line balance score 0–100: ideal when cook and plate both engaged
        if n == 0:
            balance = 15.0
        else:
            cook_c = min(1.0, cook_staff / 2.0)
            plate_c = min(1.0, plating_staff / 2.0)
            motion_c = min(1.0, frame_motion * 12.0 + mean_speed / 80.0)
            food_c = min(1.0, food_visible / 3.0)
            # Prefer both stations used; penalize extreme imbalance
            both = min(cook_c, plate_c) if (cook_c > 0 or plate_c > 0) else 0.0
            imbalance = abs(cook_c - plate_c)
            balance = 100.0 * (
                0.35 * both
                + 0.25 * cook_c
                + 0.20 * plate_c
                + 0.15 * motion_c
                + 0.05 * food_c
                - 0.15 * imbalance
            )
            balance = max(0.0, min(100.0, balance))

        if cooking_load == "rush" and plating_pressure == "backed_up":
            line_pace = "critical"
        elif cooking_load == "rush" or plating_pressure == "backed_up":
            line_pace = "fast"
        elif cooking_load == "active" or plating_pressure == "plating":
            line_pace = "steady"
        elif cooking_load == "light":
            line_pace = "slow"
        else:
            line_pace = "idle"

        return {
            "cook_staff_est": cook_staff,
            "plating_staff_est": plating_staff,
            "cook_active_stations": cook_active_stations,
            "cooking_load": cooking_load,
            "plating_pressure": plating_pressure,
            "line_pace": line_pace,
            "line_balance_score": round(balance, 1),
            "food_objects_visible": food_visible,
            "station_motion": motion_label,
            "mean_hand_speed_px_s": round(mean_speed, 1),
            "frame_motion_energy": round(frame_motion, 4),
            "floor_coverage": round(coverage, 4),
            "stations": station_detail,
            "line_hint": self._kitchen_line_hint(
                cooking_load, plating_pressure, cook_staff, plating_staff, n
            ),
            "disclaimer": (
                "Kitchen proxies from station zones + person tracks + motion + COCO food. "
                "Not ticket times or true dish identity."
            ),
        }

    def _kitchen_line_hint(
        self,
        cooking_load: str,
        plating_pressure: str,
        cook_staff: int,
        plating_staff: int,
        n: int,
    ) -> str:
        if n == 0:
            return "No staff in frame — check camera / closed / POV angle"
        if cooking_load == "rush" and plating_pressure == "backed_up":
            return "Line in the weeds — call expo help, hold new tickets"
        if cooking_load == "rush" and plating_pressure == "clear":
            return "Grill hot, pass clear — push finished plates to expo"
        if cooking_load == "idle" and plating_pressure in ("plating", "backed_up"):
            return "Pass busy, cook quiet — finish plating / wipe pass"
        if cooking_load == "active" and plating_pressure == "plating":
            return "Balanced cook + plate — maintain ticket pace"
        if cooking_load == "active":
            return "Cooking active — stage plates, keep pass clear"
        if plating_pressure == "plating":
            return "Plating in progress — call runners if tickets pile"
        if cooking_load == "light":
            return "Light grill activity — prep for next wave"
        return "Quiet line — restock mise, clean, prep garnishes"

    def _staffing_hint(self, load: str, n: int, occ_rate: float) -> str:
        st = self.config.scene_type
        if st == "pub_bar":
            if load == "very_busy":
                return "Packed pub — double bar & clear glasses fast"
            if load == "busy":
                return "Busy bar night — watch service queue & tables"
            if load == "sparse":
                return "Quiet pint hour — prep garnish / restock"
            return "Steady pub traffic — normal bar service"
        if st == "kitchen_line":
            if load == "very_busy":
                return "Kitchen rush — all hands on cook + pass"
            if load == "busy":
                return "Busy line — keep cook and plating in sync"
            if load == "sparse":
                return "Quiet kitchen — prep mise / deep clean stations"
            return "Steady kitchen — balance grill and plating"
        if load == "very_busy":
            return "High floor load — prioritize seating & table turns"
        if load == "busy":
            return "Busy dining floor — watch queue and service speed"
        if load == "sparse" and occ_rate < 0.3:
            return "Light traffic — good window for reset / prep"
        if n == 0:
            return "No people detected in frame (camera angle / empty)"
        return "Moderate activity — maintain normal service"

    def _pub_service_hint(self, load: str, bar_people: int, n: int) -> str:
        if bar_people >= 4 or load == "very_busy":
            return "Bar rush — open second pour / call backup"
        if bar_people >= 2:
            return "Active bar rail — keep taps & tills moving"
        if n >= 3 and bar_people == 0:
            return "Floor busy, bar quiet — table service focus"
        if n == 0:
            return "Empty frame — check camera / closed hour"
        return "Relaxed service — engage regulars"

    def maybe_improve(self) -> bool:
        """Every improve_interval_sec, retune thresholds from rolling history."""
        cfg = self.config
        now = time.time()
        if now - self._last_improve_ts < cfg.improve_interval_sec:
            return False
        if len(self._person_hist) < 30:
            self._last_improve_ts = now
            return False

        hist = sorted(self._person_hist)
        avg = sum(hist) / len(hist)
        peak = max(hist)
        # Percentile-based: busy ~ p60, very_busy ~ p85 (not avg+1 which never fires)
        def _pct(p: float) -> float:
            if not hist:
                return 0.0
            idx = min(len(hist) - 1, max(0, int(round(p * (len(hist) - 1)))))
            return float(hist[idx])

        p60 = _pct(0.60)
        p85 = _pct(0.85)
        # busy when above typical; keep scene-type floor
        floor_busy = 3 if cfg.scene_type == "kitchen_line" else 4
        new_busy = max(floor_busy, int(round(p60 + 0.5)))
        new_very = max(new_busy + 2, int(round(max(p85, peak * 0.85))))
        new_sparse = max(0, min(2, new_busy - 3))
        # Don't let thresholds drift absurdly vs observed avg
        new_busy = int(min(new_busy, max(floor_busy, avg + 2)))
        new_very = int(max(new_busy + 2, min(new_very, peak + 1 if peak else new_busy + 3)))

        cov = list(self._coverage_hist)
        avg_cov = sum(cov) / len(cov) if cov else cfg.floor_coverage_busy
        new_cov = max(0.05, min(0.35, avg_cov * 1.25))

        note = (
            f"v{cfg.version + 1}: retuned from {len(hist)} samples "
            f"avg_persons={avg:.1f} peak={peak} p60={p60:.0f} → busy>={new_busy} very>={new_very}"
        )
        cfg.busy_person_threshold = new_busy
        cfg.very_busy_person_threshold = new_very
        cfg.sparse_person_threshold = new_sparse
        cfg.floor_coverage_busy = round(new_cov, 3)
        cfg.version += 1
        cfg.notes.append(note)
        self._last_improve_ts = now
        self._persist()
        return True

    def force_improve_for_test(self) -> bool:
        self._last_improve_ts = 0
        return self.maybe_improve()

    def _persist(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": self.config.to_dict(),
            "peak_persons": self._peak_persons,
            "last_improve_ts": self._last_improve_ts,
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
