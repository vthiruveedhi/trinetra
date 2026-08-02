#!/usr/bin/env python3
"""Run RasaOps pipeline on two RTSP cameras in parallel."""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(threadName)-10s] %(levelname)-8s %(message)s"
)
log = logging.getLogger(__name__)


def run_camera(camera_id: str, rtsp_url: str, zones_path: str, frames: int, backend: str):
    """Run pipeline on one camera (in a thread)."""
    import os
    os.environ["RASAOPS_RTSP_URL"] = rtsp_url
    os.environ["RASAOPS_INFERENCE_BACKEND"] = backend

    try:
        from rasaops_edge.capture.rtsp_source import RTSPSource
        from rasaops_edge.pipeline import EdgePipeline

        rtsp = RTSPSource.from_env()
        pipeline = EdgePipeline.from_paths(zones_path, device_id=f"mac-{camera_id}")

        log.info(f"Camera {camera_id}: starting ({rtsp_url[-30:]}...)")

        frame_count = 0
        start = time.time()
        for frame in rtsp.frames():
            frame_count += 1
            result = pipeline.process_bgr(frame.bgr, timestamp_ms=frame.timestamp_ms, scrub=False)

            if frame_count % 10 == 0:
                elapsed = time.time() - start
                fps = frame_count / elapsed if elapsed > 0 else 0
                dets = result.metrics.get("detection_count", 0)
                tracks = result.metrics.get("track_count", 0)
                log.info(f"Camera {camera_id}: frame {frame_count:3d} | dets={dets} tracks={tracks} fps={fps:.1f}")

            if frame_count >= frames:
                break

        elapsed = time.time() - start
        log.info(f"Camera {camera_id}: done. {frame_count} frames in {elapsed:.1f}s")

    except Exception as e:
        log.exception(f"Camera {camera_id} error: {e}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cam1-url", required=True, help="Camera 1 RTSP URL")
    p.add_argument("--cam2-url", required=True, help="Camera 2 RTSP URL")
    p.add_argument("--cam1-zones", required=True, help="Camera 1 zones config")
    p.add_argument("--cam2-zones", required=True, help="Camera 2 zones config")
    p.add_argument("--frames", type=int, default=30, help="Frames per camera")
    p.add_argument("--backend", default="hog", help="Inference backend")
    args = p.parse_args()

    log.info("Starting dual-camera pipeline...")
    log.info("")

    t1 = threading.Thread(
        target=run_camera,
        args=("cam1", args.cam1_url, args.cam1_zones, args.frames, args.backend),
        name="Camera1",
    )
    t2 = threading.Thread(
        target=run_camera,
        args=("cam2", args.cam2_url, args.cam2_zones, args.frames, args.backend),
        name="Camera2",
    )

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    log.info("")
    log.info("✓ Dual-camera pipeline complete")


if __name__ == "__main__":
    main()
