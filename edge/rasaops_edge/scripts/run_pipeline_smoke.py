"""PR-04/05/06 smoke: mock or ONNX → track → occupancy events → optional scrub."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "edge"))
sys.path.insert(0, str(_ROOT / "shared" / "python"))

from rasaops_edge.inference.mock import MockInferenceBackend, person
from rasaops_edge.pipeline import EdgePipeline
from rasaops_edge.privacy import MockFaceDetector, PrivacyConfig, PrivacyScrubber
from rasaops_shared.events import EventType


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--zones",
        default=str(_ROOT / "shared" / "schemas" / "examples" / "zones.v1.sample.json"),
    )
    p.add_argument(
        "--mode",
        choices=["synthetic", "auto"],
        default="synthetic",
        help="synthetic uses scripted boxes on table-1; auto uses create_backend()",
    )
    p.add_argument("--frames", type=int, default=12)
    p.add_argument(
        "--scrub",
        action="store_true",
        help="Run PR-06 fail-closed scrubber on synthetic blank frames",
    )
    args = p.parse_args(argv)

    # Sample zones: table-1 polygon [[40,200]...[180,360]] — center ~110,280
    seq = []
    # Frames 0-7: person inside table-1 (should occupy after ~3s @ 2fps ≈ 6 frames)
    for _ in range(8):
        seq.append([person((80, 240, 140, 320), conf=0.92)])
    # Frames 8-20: empty (leave after 10s — may not free in short smoke)
    for _ in range(max(0, args.frames - 8)):
        seq.append([])

    backend = MockInferenceBackend(detections_by_frame=seq)
    scrubber = None
    if args.scrub:
        # Zero faces → head-ROI heuristic on person boxes (back-view path)
        scrubber = PrivacyScrubber(
            config=PrivacyConfig(frames_upload_entitled=True),
            face_detector=MockFaceDetector(faces=[]),
        )
    pipe = EdgePipeline.from_paths(
        args.zones,
        backend=backend,
        device_id="lab-device-1",
        scrubber=scrubber,
        frames_upload_entitled=bool(args.scrub),
    )
    # Speed up smoke: 1s enter so fewer frames
    pipe.config.t_enter_sec = 1.0
    pipe.config.t_leave_sec = 2.0
    pipe.config.sample_fps = 2.0
    pipe.occupancy.config = pipe.config

    all_events = []
    last_scrub_status = None
    t0 = int(time.time() * 1000)
    for i in range(args.frames):
        # 500 ms steps @ 2 FPS
        ts = t0 + i * 500
        if args.mode == "synthetic":
            dets = seq[min(i, len(seq) - 1)]
            result = pipe.process_synthetic(dets, timestamp_ms=ts, scrub=args.scrub)
        else:
            import numpy as np

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = pipe.process_bgr(frame, timestamp_ms=ts, scrub=args.scrub)
        if result.scrub is not None:
            last_scrub_status = result.scrub.redaction_status.value
        if result.events:
            for ev in result.events:
                all_events.append(ev.event_type.value)
                print(
                    f"frame={i} event={ev.event_type.value} zone={ev.zone.zone_id if ev.zone else None}"
                    + (f" redaction={ev.redaction_status.value}" if args.scrub else "")
                )
        else:
            states = ",".join(
                f"{t['zone_id']}={t['state']}/{t['person_count_est']}"
                for t in result.metrics["tables"]
            )
            extra = f" scrub={last_scrub_status}" if args.scrub else ""
            print(f"frame={i} tracks={result.metrics['track_count']} tables=[{states}]{extra}")

    occupied = EventType.TABLE_OCCUPIED.value in all_events
    print(f"events={all_events}")
    print(f"table_occupied_emitted={occupied}")
    if args.scrub:
        print(f"last_redaction_status={last_scrub_status}")
    if not occupied:
        print("FAIL: expected table_occupied")
        return 1
    print("run_pipeline_smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
