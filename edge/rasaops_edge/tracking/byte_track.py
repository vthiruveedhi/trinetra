"""ByteTrack-lite: anonymous short-lived IDs for occupancy association only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from rasaops_edge.inference.backend import Detection


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / (area_a + area_b - inter + 1e-6)


@dataclass
class Track:
    track_id: int
    xyxy: Tuple[float, float, float, float]
    conf: float
    class_name: str
    hits: int = 1
    age_frames: int = 1
    time_since_update: int = 0
    created_ts_ms: int = 0
    last_ts_ms: int = 0

    @property
    def centroid(self) -> Tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass
class ByteTrackLite:
    """
    Simplified IoU tracker (ByteTrack-inspired).

    - Greedy match by IoU
    - max_track_sec: hard TTL (architecture default 120s)
    - IDs are local-only; never ship to cloud event packages
    """

    match_iou: float = 0.25
    max_age_frames: int = 45  # miss budget before delete (helps low-FPS agents)
    min_hits: int = 1
    max_track_sec: float = 120.0
    high_thresh: float = 0.35
    _next_id: int = field(default=1, init=False, repr=False)
    _tracks: Dict[int, Track] = field(default_factory=dict, init=False, repr=False)

    def update(
        self,
        detections: List[Detection],
        timestamp_ms: int,
        person_only: bool = True,
    ) -> List[Track]:
        dets = [
            d
            for d in detections
            if (not person_only or d.class_name == "person" or d.class_id == 0)
        ]
        # Prefer higher-confidence first
        dets = sorted(dets, key=lambda d: d.conf, reverse=True)

        track_ids = list(self._tracks.keys())
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()

        # Greedy IoU matching
        pairs: List[Tuple[float, int, int]] = []
        for ti, tid in enumerate(track_ids):
            tr = self._tracks[tid]
            for di, det in enumerate(dets):
                pairs.append((_iou(tr.xyxy, det.xyxy), ti, di))
        pairs.sort(key=lambda x: x[0], reverse=True)

        for iou, ti, di in pairs:
            tid = track_ids[ti]
            if iou < self.match_iou:
                break
            if tid in matched_tracks or di in matched_dets:
                continue
            det = dets[di]
            tr = self._tracks[tid]
            tr.xyxy = det.xyxy
            tr.conf = det.conf
            tr.class_name = det.class_name
            tr.hits += 1
            tr.age_frames += 1
            tr.time_since_update = 0
            tr.last_ts_ms = timestamp_ms
            matched_tracks.add(tid)
            matched_dets.add(di)

        # Unmatched tracks age
        for tid in track_ids:
            if tid not in matched_tracks:
                tr = self._tracks[tid]
                tr.time_since_update += 1
                tr.age_frames += 1

        # Birth new tracks
        for di, det in enumerate(dets):
            if di in matched_dets:
                continue
            if det.conf < self.high_thresh and self._tracks:
                # still allow birth if no tracks yet / low conf room
                if det.conf < 0.25:
                    continue
            tid = self._next_id
            self._next_id += 1
            self._tracks[tid] = Track(
                track_id=tid,
                xyxy=det.xyxy,
                conf=det.conf,
                class_name=det.class_name,
                hits=1,
                age_frames=1,
                time_since_update=0,
                created_ts_ms=timestamp_ms,
                last_ts_ms=timestamp_ms,
            )

        # Prune by miss age and wall-clock TTL
        dead: List[int] = []
        for tid, tr in self._tracks.items():
            if tr.time_since_update > self.max_age_frames:
                dead.append(tid)
                continue
            lifetime_s = (timestamp_ms - tr.created_ts_ms) / 1000.0
            if lifetime_s > self.max_track_sec:
                dead.append(tid)
        for tid in dead:
            del self._tracks[tid]

        # Confirmed tracks only
        return [
            tr
            for tr in self._tracks.values()
            if tr.hits >= self.min_hits and tr.time_since_update == 0
        ]

    def active_tracks(self) -> List[Track]:
        return list(self._tracks.values())
