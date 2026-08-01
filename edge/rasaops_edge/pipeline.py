"""Edge vision pipeline: capture → infer → track → occupancy → scrub (PR-04/05/06)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from rasaops_edge.events.occupancy import OccupancyConfig, OccupancyEngine
from rasaops_edge.inference.backend import Detection, InferenceBackend
from rasaops_edge.inference.factory import create_backend
from rasaops_edge.privacy.scrubber import (
    PrivacyConfig,
    PrivacyScrubber,
    ScrubResult,
    person_boxes_from_detections,
)
from rasaops_edge.tracking.byte_track import ByteTrackLite, Track
from rasaops_shared.events import EventMeta, LocalMetricsSnapshot
from rasaops_shared.zones import ZonesDocument, load_zones


@dataclass
class PipelineFrameResult:
    timestamp_ms: int
    detections: List[Detection]
    tracks: List[Track]
    events: List[EventMeta]
    metrics: Dict[str, Any]
    inference_ms: float = 0.0
    scrub: Optional[ScrubResult] = None


@dataclass
class EdgePipeline:
    zones: ZonesDocument
    backend: InferenceBackend
    tracker: ByteTrackLite = field(default_factory=ByteTrackLite)
    occupancy: OccupancyEngine = field(init=False)
    config: OccupancyConfig = field(default_factory=OccupancyConfig)
    scrubber: Optional[PrivacyScrubber] = None
    privacy_config: PrivacyConfig = field(default_factory=PrivacyConfig)

    def __post_init__(self) -> None:
        self.config.site_id = self.zones.site_id
        self.config.camera_id = self.zones.camera_id or self.config.camera_id
        self.config.model_version = self.backend.model_version
        self.occupancy = OccupancyEngine(zones=self.zones, config=self.config)
        if self.scrubber is None:
            # Align entitlement with occupancy config for cloud packages
            self.privacy_config.frames_upload_entitled = self.config.frames_upload_entitled
            self.scrubber = PrivacyScrubber(config=self.privacy_config)

    @classmethod
    def from_paths(
        cls,
        zones_path: str | Path,
        backend: Optional[InferenceBackend] = None,
        device_id: str = "lab-device-1",
        scrubber: Optional[PrivacyScrubber] = None,
        privacy_config: Optional[PrivacyConfig] = None,
        **occ_kwargs: Any,
    ) -> "EdgePipeline":
        zones = load_zones(zones_path)
        be = backend or create_backend()
        cfg = OccupancyConfig(device_id=device_id, **occ_kwargs)
        pc = privacy_config or PrivacyConfig(
            frames_upload_entitled=cfg.frames_upload_entitled
        )
        return cls(
            zones=zones,
            backend=be,
            config=cfg,
            scrubber=scrubber,
            privacy_config=pc,
        )

    def scrub_frame(
        self,
        frame_bgr: Optional[np.ndarray],
        detections: Optional[List[Detection]] = None,
        person_boxes: Optional[List[tuple[float, float, float, float]]] = None,
    ) -> ScrubResult:
        """Run fail-closed scrubber (mandatory before any cloud frame upload)."""
        assert self.scrubber is not None
        boxes = person_boxes
        if boxes is None and detections is not None:
            boxes = person_boxes_from_detections(detections)
        return self.scrubber.scrub(frame_bgr, boxes)

    def process_bgr(
        self,
        frame_bgr: np.ndarray,
        timestamp_ms: int,
        *,
        scrub: bool = False,
    ) -> PipelineFrameResult:
        import time

        t0 = time.perf_counter()
        dets = self.backend.predict(frame_bgr)
        inference_ms = (time.perf_counter() - t0) * 1000.0
        tracks = self.tracker.update(dets, timestamp_ms=timestamp_ms)
        events = self.occupancy.update(tracks, timestamp_ms=timestamp_ms)
        metrics = self.occupancy.metrics_with_tracks(tracks)
        metrics["inference_ms"] = inference_ms
        metrics["detection_count"] = len(dets)
        metrics["track_count"] = len(tracks)
        scrub_result: Optional[ScrubResult] = None
        if scrub:
            scrub_result = self.scrub_frame(frame_bgr, detections=dets)
            metrics["redaction_status"] = scrub_result.redaction_status.value
            metrics["frame_upload_allowed"] = scrub_result.frame_upload_allowed
            # Stamp events with scrub outcome for queue packaging (PR-07)
            for ev in events:
                ev.redaction_status = scrub_result.redaction_status
                ev.frames_upload_entitled = scrub_result.frames_upload_entitled
        return PipelineFrameResult(
            timestamp_ms=timestamp_ms,
            detections=dets,
            tracks=tracks,
            events=events,
            metrics=metrics,
            inference_ms=inference_ms,
            scrub=scrub_result,
        )

    def process_synthetic(
        self,
        detections: List[Detection],
        timestamp_ms: int,
        frame_shape: tuple[int, int] = (480, 640),
        frame_bgr: Optional[np.ndarray] = None,
        *,
        scrub: bool = False,
    ) -> PipelineFrameResult:
        """Test helper: inject detections without running model."""
        h, w = frame_shape
        blank = frame_bgr if frame_bgr is not None else np.zeros((h, w, 3), dtype=np.uint8)
        # Bypass backend
        tracks = self.tracker.update(detections, timestamp_ms=timestamp_ms)
        events = self.occupancy.update(tracks, timestamp_ms=timestamp_ms)
        metrics = self.occupancy.metrics_with_tracks(tracks)
        metrics["detection_count"] = len(detections)
        metrics["track_count"] = len(tracks)
        scrub_result: Optional[ScrubResult] = None
        if scrub:
            scrub_result = self.scrub_frame(blank, detections=detections)
            metrics["redaction_status"] = scrub_result.redaction_status.value
            metrics["frame_upload_allowed"] = scrub_result.frame_upload_allowed
            for ev in events:
                ev.redaction_status = scrub_result.redaction_status
                ev.frames_upload_entitled = scrub_result.frames_upload_entitled
        return PipelineFrameResult(
            timestamp_ms=timestamp_ms,
            detections=detections,
            tracks=tracks,
            events=events,
            metrics=metrics,
            inference_ms=0.0,
            scrub=scrub_result,
        )

    def local_metrics_snapshot(self, tracks: List[Track]) -> LocalMetricsSnapshot:
        m = self.occupancy.metrics_with_tracks(tracks)
        return LocalMetricsSnapshot(
            site_id=self.config.site_id,
            device_id=self.config.device_id,
            tables=m["tables"],
            entrance_queue_count=m["entrance_queue_count"],
            pending_actions=[],
            queue_depth=0,
        )
