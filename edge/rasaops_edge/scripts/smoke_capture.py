"""PR-03 smoke: open FileVideoSource + load zones + emit one synthetic event meta."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running without install
_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "edge"))
sys.path.insert(0, str(_ROOT / "shared" / "python"))

from rasaops_edge.capture.file_source import FileVideoSource
from rasaops_shared.events import EventMeta, EventPackage, EventType, RedactionStatus
from rasaops_shared.zones import load_zones, tables


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RasaOps edge capture smoke test")
    parser.add_argument(
        "--video",
        default=os.environ.get(
            "RASAOPS_VIDEO_PATH",
            str(_ROOT / "devops" / "virtual-pi" / "fixtures" / "sample_dining.mp4"),
        ),
    )
    parser.add_argument(
        "--zones",
        default=os.environ.get(
            "RASAOPS_ZONES_PATH",
            str(_ROOT / "shared" / "schemas" / "examples" / "zones.v1.sample.json"),
        ),
    )
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args(argv)

    zones = load_zones(args.zones)
    table_count = len(tables(zones))
    print(f"zones: site={zones.site_id} tables={table_count}")

    video_path = Path(args.video)
    if not video_path.exists():
        print(
            f"NOTE: video missing at {video_path} — running stub frames "
            "(install ffmpeg + Generate-TestPattern.ps1 for real video)."
        )
        # Still exercise Frame path without file by creating empty path handling
        # FileVideoSource requires file — write zero-byte marker only if missing
        # Use synthetic path that open() would fail; instead skip open and synthesize
        from rasaops_edge.capture.file_source import Frame
        import time

        for i in range(args.frames):
            f = Frame(
                timestamp_ms=int(time.time() * 1000),
                width=640,
                height=480,
                path_hint="synthetic",
            )
            print(f"frame[{i}] {f.width}x{f.height} ts={f.timestamp_ms}")
    else:
        src = FileVideoSource(video_path, target_fps=2.0)
        src.open()
        try:
            for i, frame in enumerate(src.frames()):
                print(f"frame[{i}] {frame.width}x{frame.height} ts={frame.timestamp_ms}")
                if i + 1 >= args.frames:
                    break
        finally:
            src.close()

    meta = EventMeta(
        event_type=EventType.TABLE_OCCUPIED,
        site_id=zones.site_id,
        device_id=os.environ.get("RASAOPS_DEVICE_ID", "lab-device-1"),
        camera_id=zones.camera_id or "cam-primary",
        confidence=0.85,
        metrics={"table_count_configured": table_count},
        frames_upload_entitled=True,
        redaction_status=RedactionStatus.SKIPPED_META_ONLY,
        model_version="stub-no-onnx",
    )
    pkg = EventPackage(meta=meta)
    print("event_package:")
    print(json.dumps(pkg.model_dump(mode="json"), indent=2, default=str))
    print("smoke_capture: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
