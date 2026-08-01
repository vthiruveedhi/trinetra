"""
Live host-PC dogfood: USB webcam → person detect → track → occupancy → optional scrub.

Uses OpenCV HOG by default when product YOLO ONNX is not present.
Zones are the sample dining layout (640×480); frame is letterboxed/resized to match
so you can walk into the green Table-1 polygon on screen.

Usage (PowerShell):
  cd C:\\Users\\tvikr\\restaurant-ops-ai
  .\\.venv\\Scripts\\Activate.ps1
  python -m rasaops_edge.scripts.run_webcam_dogfood
  python -m rasaops_edge.scripts.run_webcam_dogfood --list-cameras
  python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0 --scrub --fps 2

Keys in the preview window:
  q / ESC  quit
  s        toggle scrub status print
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "edge"))
sys.path.insert(0, str(_ROOT / "shared" / "python"))

from rasaops_edge.capture.webcam_source import WebcamSource, list_camera_indices
from rasaops_edge.inference.factory import create_backend
from rasaops_edge.pipeline import EdgePipeline
from rasaops_edge.privacy import MockFaceDetector, PrivacyConfig, PrivacyScrubber
from rasaops_edge.privacy.face_detector import FaceBox
from rasaops_shared.zones import load_zones


def _letterbox(
    bgr: np.ndarray, tw: int, th: int
) -> Tuple[np.ndarray, float, int, int]:
    """Resize keeping aspect; pad to tw×th. Returns canvas, scale, pad_x, pad_y."""
    import cv2

    h, w = bgr.shape[:2]
    scale = min(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    pad_x = (tw - nw) // 2
    pad_y = (th - nh) // 2
    canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized
    return canvas, scale, pad_x, pad_y


def _draw_overlay(
    canvas: np.ndarray,
    zones,
    tracks,
    events,
    metrics: dict,
    scrub_status: Optional[str],
    backend_name: str,
) -> np.ndarray:
    import cv2

    out = canvas.copy()
    # zones
    colors = {
        "table": (0, 200, 0),
        "entrance": (255, 180, 0),
        "queue": (0, 165, 255),
    }
    for z in zones.zones:
        pts = np.array(z.polygon, dtype=np.int32)
        color = colors.get(z.kind, (200, 200, 200))
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=2)
        x, y = pts[0]
        cv2.putText(
            out,
            z.label or z.zone_id,
            (int(x) + 4, int(y) + 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )

    # tracks / dets
    for tr in tracks:
        x1, y1, x2, y2 = (int(v) for v in tr.xyxy)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 255), 2)
        cv2.putText(
            out,
            f"id={tr.track_id} {tr.conf:.2f}",
            (x1, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )

    # HUD
    tables = metrics.get("tables") or []
    table_s = " | ".join(
        f"{t['zone_id']}={t['state']}/{t['person_count_est']}" for t in tables
    )
    lines = [
        f"backend={backend_name}  tracks={metrics.get('track_count', 0)}  "
        f"infer={metrics.get('inference_ms', 0):.0f}ms",
        f"tables: {table_s}",
        f"queue={metrics.get('entrance_queue_count', 0)}  "
        f"events={[e.event_type.value for e in events]}",
    ]
    if scrub_status:
        lines.append(f"scrub={scrub_status}")
    lines.append("Stand in green Table-1 box ~2s to fire table_occupied  |  q=quit")
    y0 = 18
    for line in lines:
        cv2.putText(
            out,
            line,
            (8, y0),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
        y0 += 18
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="RasaOps live webcam dogfood")
    p.add_argument("--camera", type=int, default=int(os.environ.get("RASAOPS_CAMERA_INDEX", "0")))
    p.add_argument("--fps", type=float, default=float(os.environ.get("RASAOPS_CAPTURE_FPS", "2.0")))
    p.add_argument(
        "--zones",
        default=str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.desk_webcam.json"
        ),
        help="Default: desk-webcam zones (full-frame table-1). Use zones.v1.sample.json for dining layout.",
    )
    p.add_argument(
        "--backend",
        default=os.environ.get("RASAOPS_INFERENCE_BACKEND", "auto"),
        help="auto|hog|onnx|mock",
    )
    p.add_argument("--scrub", action="store_true", help="Run PR-06 scrubber each frame")
    p.add_argument("--no-window", action="store_true", help="Headless (print only)")
    p.add_argument("--frames", type=int, default=0, help="Stop after N frames (0=infinite)")
    p.add_argument("--list-cameras", action="store_true")
    p.add_argument("--enter-sec", type=float, default=2.0, help="Occupancy enter dwell (faster dogfood)")
    args = p.parse_args(argv)

    if args.list_cameras:
        print("Probing cameras 0..4 …")
        for idx, ok in list_camera_indices(5):
            print(f"  index={idx}  open={ok}")
        return 0

    zones = load_zones(args.zones)
    tw = int(zones.image_width or 640)
    th = int(zones.image_height or 480)

    backend = create_backend(backend=args.backend)
    backend_name = getattr(backend, "model_version", type(backend).__name__)
    print(f"inference backend: {type(backend).__name__} ({backend_name})")
    print(f"camera index={args.camera}  target_fps={args.fps}  canvas={tw}x{th}")
    print("Tip: sit/stand inside the green Table-1 rectangle for ~2 seconds.")

    scrubber = None
    if args.scrub:
        # Live dogfood: no real face model yet → head-ROI path when persons present
        scrubber = PrivacyScrubber(
            config=PrivacyConfig(frames_upload_entitled=True),
            face_detector=MockFaceDetector(faces=[]),
        )

    pipe = EdgePipeline.from_paths(
        args.zones,
        backend=backend,
        device_id=os.environ.get("RASAOPS_DEVICE_ID", "lab-pc-webcam"),
        scrubber=scrubber,
        frames_upload_entitled=bool(args.scrub),
    )
    pipe.config.t_enter_sec = args.enter_sec
    pipe.config.t_leave_sec = max(3.0, args.enter_sec * 2)
    pipe.config.sample_fps = args.fps
    pipe.occupancy.config = pipe.config

    src = WebcamSource(device=args.camera, target_fps=args.fps, width=tw, height=th)
    try:
        src.open()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        print("Try: python -m rasaops_edge.scripts.run_webcam_dogfood --list-cameras")
        return 1

    win = "RasaOps webcam dogfood"
    if not args.no_window:
        try:
            import cv2

            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(win, tw, th)
        except Exception as exc:
            print(f"Display unavailable ({exc}); continuing headless.")
            args.no_window = True

    n = 0
    try:
        for frame in src.frames():
            if frame.bgr is None:
                continue
            canvas, _, _, _ = _letterbox(frame.bgr, tw, th)
            result = pipe.process_bgr(canvas, timestamp_ms=frame.timestamp_ms, scrub=args.scrub)
            scrub_s = (
                result.scrub.redaction_status.value if result.scrub is not None else None
            )
            for ev in result.events:
                print(
                    f"[event] {ev.event_type.value} zone={ev.zone.zone_id if ev.zone else None}"
                    + (f" scrub={ev.redaction_status.value}" if args.scrub else "")
                )

            if not args.no_window:
                import cv2

                vis = _draw_overlay(
                    canvas,
                    zones,
                    result.tracks,
                    result.events,
                    result.metrics,
                    scrub_s,
                    backend_name,
                )
                cv2.imshow(win, vis)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
            else:
                print(
                    f"frame={n} dets={result.metrics.get('detection_count')} "
                    f"tracks={result.metrics.get('track_count')} "
                    f"infer_ms={result.metrics.get('inference_ms', 0):.0f}"
                )

            n += 1
            if args.frames and n >= args.frames:
                break
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        src.close()
        if not args.no_window:
            try:
                import cv2

                cv2.destroyAllWindows()
            except Exception:
                pass

    print(f"done frames={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
