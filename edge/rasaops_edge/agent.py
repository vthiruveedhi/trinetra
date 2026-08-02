"""
Full edge agent loop (L1):

  capture (webcam | youtube) → infer → track → occupancy → scene metrics
  → scrub → queue → sync + dashboard state
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np

from rasaops_edge.capture.rtsp_source import RTSPSource
from rasaops_edge.capture.webcam_source import WebcamSource
from rasaops_edge.capture.youtube_source import YoutubeSource
from rasaops_edge.dashboard_api.app import DashboardState
from rasaops_edge.inference.factory import create_backend
from rasaops_edge.metrics.scene_metrics import SceneMetricsEngine, config_for_scene
from rasaops_edge.pipeline import EdgePipeline
from rasaops_edge.privacy import MockFaceDetector, PrivacyConfig, PrivacyScrubber
from rasaops_edge.queue.sqlite_queue import EventQueue
from rasaops_edge.sync.agent import SyncAgent
from rasaops_shared.events import RedactionStatus


@dataclass
class EdgeAgentConfig:
    zones_path: str | Path
    device_id: str = "lab-pc-webcam"
    camera_index: int = 0
    youtube_url: Optional[str] = None
    rtsp_url: Optional[str] = None
    source: str = "webcam"  # webcam | youtube | rtsp
    fps: float = 2.0
    enter_sec: float = 2.0
    leave_sec: float = 4.0
    data_dir: str | Path = "data/edge"
    cloud_base_url: str = "http://127.0.0.1:18080"
    frames_upload_entitled: bool = True
    backend: str = "auto"
    sync_every_sec: float = 3.0
    scrub: bool = True
    improve_interval_sec: float = 20 * 60  # 20 minutes
    site_id: str = "lab-site-001"
    scene_type: str = "dining_restaurant"


@dataclass
class EdgeAgent:
    config: EdgeAgentConfig
    state: DashboardState = field(default_factory=DashboardState)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def __post_init__(self) -> None:
        data = Path(self.config.data_dir)
        data.mkdir(parents=True, exist_ok=True)
        self.queue = EventQueue(data / "event_queue.sqlite", frames_dir=data / "frames")
        self.state.site_id = self.config.site_id
        self.state.device_id = self.config.device_id

        privacy = PrivacyConfig(frames_upload_entitled=self.config.frames_upload_entitled)
        scrubber = PrivacyScrubber(
            config=privacy,
            face_detector=MockFaceDetector(faces=[]),
        )
        backend = create_backend(
            backend=self.config.backend,
            atmosphere=self.config.scene_type
            in ("pub_bar", "dining_restaurant", "kitchen_line"),
        )
        self.pipeline = EdgePipeline.from_paths(
            self.config.zones_path,
            backend=backend,
            device_id=self.config.device_id,
            scrubber=scrubber,
            frames_upload_entitled=self.config.frames_upload_entitled,
        )
        self.pipeline.config.t_enter_sec = self.config.enter_sec
        self.pipeline.config.t_leave_sec = self.config.leave_sec
        self.pipeline.config.sample_fps = self.config.fps
        self.pipeline.config.site_id = self.config.site_id
        self.pipeline.occupancy.config = self.pipeline.config

        self.sync = SyncAgent(
            self.queue,
            cloud_base_url=self.config.cloud_base_url,
            device_id=self.config.device_id,
            site_id=self.state.site_id,
        )
        scene_cfg = config_for_scene(
            self.config.scene_type,
            improve_interval_sec=self.config.improve_interval_sec,
        )
        self.scene = SceneMetricsEngine(
            config=scene_cfg,
            state_path=data / f"scene_metrics_{self.config.scene_type}.json",
        )
        self._src: Optional[Union[WebcamSource, YoutubeSource, RTSPSource]] = None
        self.source_label: str = self.config.source

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_loop, name="edge-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)
        if self._src:
            self._src.close()
        self.queue.close()

    def run_loop(self) -> None:
        cfg = self.config
        if cfg.source == "rtsp" or cfg.rtsp_url:
            url = cfg.rtsp_url or ""
            if not url:
                raise RuntimeError("rtsp source requires rtsp_url")
            self._src = RTSPSource(url=url, target_fps=cfg.fps)
            self.source_label = f"rtsp:{url.split('@')[-1]}"  # mask credentials
        elif cfg.source == "youtube" or cfg.youtube_url:
            url = cfg.youtube_url or ""
            if not url:
                raise RuntimeError("youtube source requires youtube_url")
            self._src = YoutubeSource(page_url=url, target_fps=cfg.fps, max_height=720)
            self.source_label = f"youtube:{url}"
        else:
            self._src = WebcamSource(
                device=cfg.camera_index,
                target_fps=cfg.fps,
                width=640,
                height=480,
            )
            self.source_label = f"webcam:{cfg.camera_index}"

        self._src.open()
        last_sync = 0.0
        try:
            for frame in self._src.frames():
                if self._stop.is_set():
                    break
                if frame.bgr is None:
                    continue
                canvas = self._letterbox(frame.bgr, 640, 480)
                result = self.pipeline.process_bgr(
                    canvas, timestamp_ms=frame.timestamp_ms, scrub=cfg.scrub
                )
                scene = self.scene.update(
                    tracks=result.tracks,
                    detections=result.detections,
                    frame_shape=canvas.shape[:2],
                    timestamp_ms=frame.timestamp_ms,
                    inference_ms=result.inference_ms,
                    table_metrics=result.metrics.get("tables"),
                    frame_bgr=canvas,
                    pass_stations=result.metrics.get("pass_stations") or [],
                    pass_staff_count=int(result.metrics.get("pass_staff_count") or 0),
                    bar_crowd_est=int(result.metrics.get("bar_crowd_est") or 0),
                    seating=result.metrics.get("seating") or {},
                    queue_zones=result.metrics.get("queue_zones") or [],
                    detection_count=sum(
                        1
                        for d in result.detections
                        if (d.class_name or "") == "person" or d.class_id == 0
                    ),
                )
                metrics = dict(result.metrics)
                metrics["scene"] = scene
                metrics["source"] = self.source_label
                # Promote seating + counts for kiosk top-level
                metrics["seating"] = result.metrics.get("seating") or scene
                metrics["person_count"] = scene.get("person_count")
                metrics["bar_crowd_est"] = (
                    (scene.get("pub") or {}).get("bar_crowd_est")
                    or result.metrics.get("bar_crowd_est")
                    or 0
                )
                if isinstance(self._src, YoutubeSource):
                    metrics["stream_title"] = self._src.title

                self.state.update_metrics(metrics, queue_depth=self.queue.depth())
                self.state.source = self.source_label

                for ev in result.events:
                    frame_jpeg = None
                    scrub_ok = False
                    if result.scrub is not None:
                        ev.redaction_status = result.scrub.redaction_status
                        ev.frames_upload_entitled = result.scrub.frames_upload_entitled
                        scrub_ok = result.scrub.frame_upload_allowed
                        if scrub_ok and result.scrub.frame_bgr is not None:
                            frame_jpeg = self._encode_jpeg(result.scrub.frame_bgr)
                    else:
                        ev.redaction_status = RedactionStatus.SKIPPED_META_ONLY
                        scrub_ok = False

                    # enrich event metrics with scene snapshot
                    ev.metrics = {
                        **(ev.metrics or {}),
                        "person_count": scene.get("person_count"),
                        "floor_load": scene.get("floor_load"),
                    }
                    self.queue.enqueue(ev, frame_jpeg=frame_jpeg, scrub_allows_frame=scrub_ok)
                    self.state.push_event(
                        {
                            "event_type": ev.event_type.value,
                            "timestamp": ev.timestamp.isoformat(),
                            "zone": ev.zone.model_dump() if ev.zone else None,
                            "redaction_status": ev.redaction_status.value,
                            "event_id": ev.event_id,
                            "floor_load": scene.get("floor_load"),
                        }
                    )

                if scene.get("improved_this_tick"):
                    self.state.push_event(
                        {
                            "event_type": "metrics_improved",
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "zone": None,
                            "redaction_status": "skipped_meta_only",
                            "event_id": f"improve-v{scene.get('metric_version')}",
                            "note": scene.get("last_improve_note"),
                        }
                    )

                now = time.time()
                if now - last_sync >= cfg.sync_every_sec:
                    last_sync = now
                    sr = self.sync.drain_once()
                    self.state.online = sr.acked > 0 or not sr.offline
                    self.state.last_sync = {
                        "attempted": sr.attempted,
                        "acked": sr.acked,
                        "failed": sr.failed,
                        "offline": sr.offline,
                        "error": sr.last_error,
                    }
                    self.queue.purge_expired_frames()
                    self.state.queue_depth = self.queue.depth()
        finally:
            if self._src:
                self._src.close()
                self._src = None

    @staticmethod
    def _letterbox(bgr: np.ndarray, tw: int, th: int) -> np.ndarray:
        import cv2

        h, w = bgr.shape[:2]
        scale = min(tw / w, th / h)
        nw, nh = int(round(w * scale)), int(round(h * scale))
        resized = cv2.resize(bgr, (nw, nh))
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        px, py = (tw - nw) // 2, (th - nh) // 2
        canvas[py : py + nh, px : px + nw] = resized
        return canvas

    @staticmethod
    def _encode_jpeg(bgr: np.ndarray) -> Optional[bytes]:
        try:
            import cv2

            ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return None
            return buf.tobytes()
        except Exception:
            return None
