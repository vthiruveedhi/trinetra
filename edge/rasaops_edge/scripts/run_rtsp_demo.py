#!/usr/bin/env python3
"""
Run the RasaOps edge pipeline live against an RTSP camera.

Usage:
    python -m rasaops_edge.scripts.run_rtsp_demo \
      --rtsp "rtsp://user:pass@192.168.68.55:554/live/ch0" \
      --zones edge/config/zones.v1.json \
      --frames 60

Or set env:
    export RASAOPS_RTSP_URL="rtsp://user:pass@192.168.68.55:554/live/ch0"
    export RASAOPS_CAPTURE_FPS=2.0
    python -m rasaops_edge.scripts.run_rtsp_demo --zones edge/config/zones.v1.json
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--rtsp",
        help="RTSP URL. If not provided, read from RASAOPS_RTSP_URL env.",
    )
    p.add_argument(
        "--zones",
        default="edge/config/zones.v1.json",
        help="Zones config file (default: edge/config/zones.v1.json)",
    )
    p.add_argument(
        "--frames",
        type=int,
        default=60,
        help="Process N frames then exit (default: 60)",
    )
    p.add_argument(
        "--backend",
        default="hog",
        help="Inference backend: hog (OpenCV) | onnx | mock (default: hog)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print every frame's detections and events",
    )
    args = p.parse_args()

    import os
    if args.rtsp:
        os.environ["RASAOPS_RTSP_URL"] = args.rtsp
    if args.backend:
        os.environ["RASAOPS_INFERENCE_BACKEND"] = args.backend

    try:
        from rasaops_edge.capture.rtsp_source import RTSPSource
        from rasaops_edge.pipeline import EdgePipeline
    except ImportError as e:
        log.error("Failed to import RasaOps modules: %s", e)
        log.error("Run: pip install -e '.[edge]' in the monorepo root")
        sys.exit(1)

    zones_path = Path(args.zones)
    if not zones_path.exists():
        log.error("Zones file not found: %s", zones_path)
        log.error("Create one or use: edge/config/zones.v1.json")
        sys.exit(1)

    try:
        log.info("Creating RTSP source...")
        rtsp = RTSPSource.from_env()

        log.info("Creating pipeline with zones from %s", zones_path)
        pipeline = EdgePipeline.from_paths(zones_path, device_id="mac-lab-rtsp")

        log.info("Pipeline ready. Processing %d frames from %s", args.frames, rtsp.url)
        log.info("Backend: %s", args.backend)
        log.info("")

        frame_count = 0
        start = time.time()

        for frame in rtsp.frames():
            frame_count += 1

            result = pipeline.process_bgr(frame.bgr, timestamp_ms=frame.timestamp_ms, scrub=False)

            elapsed_s = time.time() - start
            fps_actual = frame_count / elapsed_s if elapsed_s > 0 else 0

            # Report every 10 frames
            if frame_count % 10 == 0 or args.verbose:
                print(
                    f"[{frame_count:3d}] "
                    f"dets={result.detection_count} "
                    f"tracks={result.track_count} "
                    f"inf={result.inference_ms:.1f}ms "
                    f"fps={fps_actual:.1f} "
                    f"events={len(result.events)}"
                )

            if args.verbose and result.events:
                for ev in result.events:
                    print(f"      event: {ev.event_type} @ table={ev.table_id}")

            if frame_count >= args.frames:
                break

        log.info("")
        log.info("Done. Processed %d frames in %.1f s (%.1f FPS avg)", frame_count, elapsed_s, fps_actual)

    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        log.exception("Error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
