"""
Room atmosphere proxies (lab only).

IMPORTANT: No facial recognition, no emotion ML on faces.
"Happiness" is a **heuristic vibe score** from social clustering + motion + activity
— not true emotion measurement. Labelled as estimated / proxy in all outputs.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from rasaops_edge.inference.backend import Detection
from rasaops_edge.tracking.byte_track import Track

# COCO classes useful for pub/dining atmosphere
DRINK_CLASSES = frozenset({"bottle", "wine glass", "cup"})
FOOD_CLASSES = frozenset(
    {
        "bowl",
        "banana",
        "apple",
        "sandwich",
        "orange",
        "broccoli",
        "carrot",
        "hot dog",
        "pizza",
        "donut",
        "cake",
    }
)
TABLE_CLASSES = frozenset({"dining table", "chair"})
ATMOSPHERE_KEEP = frozenset({"person"} | DRINK_CLASSES | FOOD_CLASSES | TABLE_CLASSES)


@dataclass
class AtmosphereEngine:
    """Frame-to-frame motion + social + object proxies."""

    social_dist_px: float = 90.0  # ~close conversation distance at 640px width
    _prev_gray: Optional[np.ndarray] = field(default=None, init=False, repr=False)
    _prev_centroids: Dict[int, Tuple[float, float]] = field(default_factory=dict, init=False)
    _prev_ts_ms: Optional[int] = field(default=None, init=False)
    _motion_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    _happy_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    _drink_hist: Deque[int] = field(default_factory=lambda: deque(maxlen=120))
    _eat_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=120))

    def update(
        self,
        *,
        frame_bgr: Optional[np.ndarray],
        tracks: List[Track],
        detections: List[Detection],
        timestamp_ms: int,
        table_metrics: Optional[List[dict]] = None,
    ) -> Dict[str, Any]:
        persons = [t for t in tracks if (t.class_name or "person") == "person"]
        n = len(persons)

        # --- Movement energy (frame diff) ---
        motion = 0.0
        if frame_bgr is not None and frame_bgr.size > 0:
            try:
                import cv2

                small = cv2.resize(frame_bgr, (160, 120))
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
                    diff = cv2.absdiff(gray, self._prev_gray)
                    motion = float(np.mean(diff) / 255.0)
                self._prev_gray = gray
            except Exception:
                motion = 0.0
        self._motion_hist.append(motion)

        # --- Track speed (pixels/sec) ---
        speeds: List[float] = []
        cents: Dict[int, Tuple[float, float]] = {}
        dt = 0.5
        if self._prev_ts_ms is not None:
            dt = max(0.05, (timestamp_ms - self._prev_ts_ms) / 1000.0)
        for t in persons:
            cx = (t.xyxy[0] + t.xyxy[2]) / 2.0
            cy = (t.xyxy[1] + t.xyxy[3]) / 2.0
            cents[t.track_id] = (cx, cy)
            if t.track_id in self._prev_centroids:
                px, py = self._prev_centroids[t.track_id]
                dist = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                speeds.append(dist / dt)
        self._prev_centroids = cents
        self._prev_ts_ms = timestamp_ms
        mean_speed = float(sum(speeds) / len(speeds)) if speeds else 0.0
        moving_people = sum(1 for s in speeds if s > 15.0)
        stationary_people = max(0, n - moving_people)

        # --- Social clustering (proxy for "happy / social") ---
        pairs_close = 0
        pairs_total = 0
        group_members = set()
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                pairs_total += 1
                a = persons[i]
                b = persons[j]
                ax = (a.xyxy[0] + a.xyxy[2]) / 2.0
                ay = (a.xyxy[1] + a.xyxy[3]) / 2.0
                bx = (b.xyxy[0] + b.xyxy[2]) / 2.0
                by = (b.xyxy[1] + b.xyxy[3]) / 2.0
                d = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                if d <= self.social_dist_px:
                    pairs_close += 1
                    group_members.add(a.track_id)
                    group_members.add(b.track_id)
        social_ratio = (pairs_close / pairs_total) if pairs_total else 0.0
        in_groups = len(group_members)
        alone = max(0, n - in_groups)

        # --- Drink / food object counts ---
        drink_counts: Dict[str, int] = defaultdict(int)
        food_counts: Dict[str, int] = defaultdict(int)
        tables_seen = 0
        for d in detections:
            name = d.class_name or ""
            if name in DRINK_CLASSES:
                drink_counts[name] += 1
            elif name in FOOD_CLASSES:
                food_counts[name] += 1
            elif name in TABLE_CLASSES:
                tables_seen += 1
        drinks_visible = int(sum(drink_counts.values()))
        food_visible = int(sum(food_counts.values()))
        self._drink_hist.append(drinks_visible)

        # Eating activity proxy: occupied tables + food objects + stationary people at tables
        occ_tables = 0
        if table_metrics:
            occ_tables = sum(1 for t in table_metrics if t.get("state") == "occupied")
        eating_score = 0.0
        eating_score += min(1.0, food_visible / 3.0) * 0.45
        eating_score += min(1.0, occ_tables / 3.0) * 0.35
        eating_score += min(1.0, stationary_people / max(1, n)) * 0.20 if n else 0.0
        self._eat_hist.append(eating_score)

        # --- Happiness / vibe proxy (0–100) ---
        # social clustering + mild motion + presence of people (not chaos)
        social_c = min(1.0, social_ratio * 2.0 + (in_groups / max(1, n)) * 0.5)
        motion_c = min(1.0, motion * 8.0)  # absdiff mean is often small
        crowd_c = min(1.0, n / 6.0)
        drink_c = min(1.0, drinks_visible / 4.0)
        # Prefer moderate motion (too still or chaotic lowers score)
        motion_sweet = 1.0 - abs(motion_c - 0.35) * 1.5
        motion_sweet = max(0.0, min(1.0, motion_sweet))

        happiness = 100.0 * (
            0.40 * social_c
            + 0.25 * motion_sweet
            + 0.20 * crowd_c
            + 0.10 * drink_c
            + 0.05 * min(1.0, eating_score)
        )
        if n == 0:
            happiness = max(0.0, happiness * 0.15)
        self._happy_hist.append(happiness)

        # Atmosphere label
        avg_motion = sum(self._motion_hist) / len(self._motion_hist) if self._motion_hist else 0
        avg_happy = sum(self._happy_hist) / len(self._happy_hist) if self._happy_hist else 0
        if n == 0:
            atmosphere = "empty"
        elif avg_happy >= 65 and avg_motion > 0.02:
            atmosphere = "lively"
        elif avg_happy >= 55 and social_c > 0.3:
            atmosphere = "warm_social"
        elif avg_motion < 0.01 and n >= 2:
            atmosphere = "calm_settled"
        elif mean_speed > 40:
            atmosphere = "bustling"
        elif n <= 2 and avg_happy < 40:
            atmosphere = "quiet"
        else:
            atmosphere = "relaxed"

        drink_rate = (
            sum(self._drink_hist) / len(self._drink_hist) if self._drink_hist else 0.0
        )
        eat_rate = sum(self._eat_hist) / len(self._eat_hist) if self._eat_hist else 0.0

        return {
            "disclaimer": (
                "Proxies only — not real emotion AI. No facial recognition. "
                "Drinks/food = visible COCO objects, not sales counts."
            ),
            "happiness_index": round(happiness, 1),
            "happiness_avg": round(avg_happy, 1),
            "happiness_label": (
                "high_vibe"
                if happiness >= 70
                else ("good" if happiness >= 50 else ("low" if happiness < 30 else "mixed"))
            ),
            "movement": {
                "frame_motion_energy": round(motion, 4),
                "frame_motion_avg": round(avg_motion, 4),
                "mean_person_speed_px_s": round(mean_speed, 1),
                "moving_people": moving_people,
                "stationary_people": stationary_people,
                "movement_label": (
                    "high"
                    if mean_speed > 40 or motion > 0.04
                    else ("medium" if mean_speed > 15 or motion > 0.015 else "low")
                ),
            },
            "social": {
                "close_pairs": pairs_close,
                "people_in_groups": in_groups,
                "people_alone": alone,
                "social_ratio": round(social_ratio, 3),
            },
            "drinks": {
                "visible_now": drinks_visible,
                "by_class": dict(drink_counts),
                "visible_avg": round(drink_rate, 2),
                "note": "Visible bottles/cups/glasses in frame — not poured drinks sold",
            },
            "eating": {
                "activity_score": round(eating_score, 3),
                "activity_avg": round(eat_rate, 3),
                "food_objects_visible": food_visible,
                "food_by_class": dict(food_counts),
                "occupied_tables": occ_tables,
                "dining_tables_detected": tables_seen,
                "label": (
                    "active"
                    if eating_score >= 0.45
                    else ("light" if eating_score >= 0.2 else "low")
                ),
                "note": "Heuristic from food objects + seated occupancy",
            },
            "atmosphere": {
                "label": atmosphere,
                "score": round(avg_happy, 1),
                "components": {
                    "social": round(social_c, 3),
                    "motion_sweet": round(motion_sweet, 3),
                    "crowd": round(crowd_c, 3),
                    "drinks": round(drink_c, 3),
                    "eating": round(min(1.0, eating_score), 3),
                },
            },
        }
