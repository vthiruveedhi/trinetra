#!/usr/bin/env python3
"""test_rtsp.py — Verify RTSP camera connectivity before hardware arrives.

Runs on any machine with OpenCV. Confirms the camera stream can be opened,
measures real FPS over a sample window, and saves a few frames to disk so
you can eyeball the image.

Usage:
    python test_rtsp.py "rtsp://user:pass@192.168.1.42:554/stream1"

Flags:
    --transport tcp|udp     Default tcp — more reliable over WiFi
    --seconds N             Sample duration in seconds (default 10)
    --save N                Save first N frames as JPGs (default 3)
    --out DIR               Where to save frames (default rtsp_test_output/)
    --show                  Show live frames in an OpenCV window
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path


def mask_password(url: str) -> str:
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Verify RTSP camera stream",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="RTSP URL, e.g. rtsp://user:pass@192.168.1.42:554/stream1")
    p.add_argument("--transport", choices=["tcp", "udp"], default="tcp",
                   help="RTSP transport (default: tcp, more reliable over WiFi)")
    p.add_argument("--seconds", type=int, default=10,
                   help="Sample duration for FPS measurement (default: 10)")
    p.add_argument("--save", type=int, default=3, metavar="N",
                   help="Save first N frames as JPGs (default: 3)")
    p.add_argument("--out", default="rtsp_test_output",
                   help="Output directory for saved frames")
    p.add_argument("--show", action="store_true",
                   help="Show live frames in an OpenCV window (press q to stop)")
    args = p.parse_args()

    # Must set BEFORE importing cv2 so FFmpeg backend picks it up
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{args.transport}"

    import cv2

    print(f"[open] {mask_password(args.url)}  (transport={args.transport})")
    cap = cv2.VideoCapture(args.url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("\n[FAIL] Could not open stream.\n")
        print("Common causes:")
        print("  - Wrong URL / username / password")
        print("  - Camera not on the same LAN as this Mac")
        print("  - Camera's RTSP server disabled in its app")
        print("  - Firewall / VPN blocking port 554")
        print("  - Some cameras only expose RTSP after enabling it in settings")
        sys.exit(1)

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    reported_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    print(f"[open] OK — {w}x{h} @ reported {reported_fps:.1f} FPS")

    out_dir = Path(args.out)
    if args.save > 0:
        out_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    frames = 0
    dropped = 0
    saved = 0

    print(f"[run] sampling for {args.seconds}s (press Ctrl+C to stop early)...")
    try:
        while time.time() - start < args.seconds:
            ret, frame = cap.read()
            if not ret:
                dropped += 1
                continue
            frames += 1

            if saved < args.save:
                jpg = out_dir / f"frame_{saved:03d}.jpg"
                cv2.imwrite(str(jpg), frame)
                saved += 1

            if args.show:
                cv2.imshow("RTSP test — press q to quit", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        print("\n[stop] user interrupt")

    elapsed = time.time() - start
    measured_fps = frames / elapsed if elapsed > 0 else 0.0
    print(f"\n[done] {frames} frames in {elapsed:.1f}s = {measured_fps:.1f} FPS measured")
    if dropped:
        print(f"       {dropped} frame reads dropped (WiFi jitter or codec hiccups)")
    if saved > 0:
        print(f"       {saved} frames saved to {out_dir}/")
        first = out_dir / "frame_000.jpg"
        if first.exists():
            print(f"       open {first} — visually confirm image is not corrupted")

    cap.release()
    if args.show:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
