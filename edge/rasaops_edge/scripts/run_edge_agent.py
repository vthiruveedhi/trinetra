"""
Run full L1 stack on host:

  python -m rasaops_edge.scripts.run_edge_agent --camera 0 --with-cloud
  python -m rasaops_edge.scripts.run_edge_agent --youtube URL --with-cloud
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "edge"))
sys.path.insert(0, str(_ROOT / "shared" / "python"))
sys.path.insert(0, str(_ROOT))

DEFAULT_YT = "https://www.youtube.com/watch?v=0JGQo-vAgwQ"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RasaOps full edge agent + kiosk")
    p.add_argument("--camera", type=int, default=0)
    p.add_argument(
        "--youtube",
        nargs="?",
        const=DEFAULT_YT,
        default=None,
        help=f"YouTube live/VOD URL (default if flag alone: {DEFAULT_YT})",
    )
    p.add_argument("--fps", type=float, default=1.0)
    p.add_argument(
        "--zones",
        default=None,
        help="zones.v1.json path (defaults: desk webcam or restaurant_live for youtube)",
    )
    p.add_argument("--port", type=int, default=8090, help="Local kiosk API port")
    p.add_argument(
        "--cloud-url",
        default=os.environ.get("RASAOPS_CLOUD_BASE_URL", "http://127.0.0.1:18080"),
    )
    p.add_argument("--with-cloud", action="store_true", help="Start in-process cloud API")
    p.add_argument("--cloud-port", type=int, default=18080)
    p.add_argument("--device-id", default=None)
    p.add_argument("--backend", default=os.environ.get("RASAOPS_INFERENCE_BACKEND", "auto"))
    p.add_argument("--no-scrub", action="store_true")
    p.add_argument(
        "--improve-minutes",
        type=float,
        default=20.0,
        help="Retune scene metric thresholds every N minutes (default 20)",
    )
    p.add_argument(
        "--scene",
        default=None,
        choices=["dining_restaurant", "pub_bar", "kitchen_line", "auto"],
        help="Scene metric pack (auto picks from URL/zones)",
    )
    args = p.parse_args(argv)

    if args.with_cloud:
        import uvicorn
        from cloud.api.app import create_app as create_cloud

        def _cloud() -> None:
            uvicorn.run(create_cloud(), host="127.0.0.1", port=args.cloud_port, log_level="warning")

        t = threading.Thread(target=_cloud, name="cloud-api", daemon=True)
        t.start()
        time.sleep(0.8)
        print(f"Cloud API: http://127.0.0.1:{args.cloud_port}/health")

    from rasaops_edge.agent import EdgeAgent, EdgeAgentConfig
    from rasaops_edge.dashboard_api.app import create_app
    import uvicorn

    use_yt = bool(args.youtube)
    yt = (args.youtube or "").lower()

    # Auto scene + zones from known demo streams
    scene = args.scene or "auto"
    if scene == "auto":
        if "0jgqo-vagwq" in yt or "cooper" in yt:
            scene = "pub_bar"
        elif (
            "sfgiattj9lc" in yt
            or "wscyqjh_5tu" in yt
            or "8zq8e13yhbk" in yt
            or "grill" in yt
            or "steak" in yt
            or "kitchen" in yt
            or "pov" in yt
            or "chef" in yt
            or "plating" in yt
            or "dinner rush" in yt
        ):
            scene = "kitchen_line"
        elif use_yt:
            scene = "dining_restaurant"
        else:
            scene = "dining_restaurant"

    if args.zones:
        zones = args.zones
    elif scene == "pub_bar":
        zones = str(_ROOT / "shared" / "schemas" / "examples" / "zones.v1.pub_live.json")
    elif scene == "kitchen_line":
        zones = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.kitchen_live.json"
        )
    elif use_yt:
        zones = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.restaurant_live.json"
        )
    else:
        zones = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.desk_webcam.json"
        )

    device_id = args.device_id or (
        "lab-youtube-coopers"
        if scene == "pub_bar"
        else (
            "lab-youtube-kitchen"
            if scene == "kitchen_line"
            else ("lab-youtube-live" if use_yt else "lab-pc-webcam")
        )
    )
    site_id = (
        "lab-youtube-coopers-pub"
        if scene == "pub_bar"
        else (
            "lab-youtube-kitchen-grill"
            if scene == "kitchen_line"
            else ("lab-youtube-live" if use_yt else "lab-site-001")
        )
    )

    cfg = EdgeAgentConfig(
        zones_path=zones,
        device_id=device_id,
        site_id=site_id,
        camera_index=args.camera,
        youtube_url=args.youtube,
        source="youtube" if use_yt else "webcam",
        fps=args.fps,
        data_dir=_ROOT / "data" / "edge",
        cloud_base_url=args.cloud_url,
        backend=args.backend,
        scrub=not args.no_scrub,
        improve_interval_sec=max(60.0, args.improve_minutes * 60.0),
        scene_type=scene,
    )
    agent = EdgeAgent(config=cfg)
    agent.start_background()
    app = create_app(agent.state)

    print(f"Kiosk UI:  http://127.0.0.1:{args.port}/")
    print(f"Metrics:   http://127.0.0.1:{args.port}/local/metrics")
    print(f"Scene:     http://127.0.0.1:{args.port}/local/scene")
    if use_yt:
        print(f"Source:    YouTube {args.youtube}")
        print(f"Scene:     {scene}")
        print(f"Zones:     {Path(zones).name}")
        print(f"Improve:   every {args.improve_minutes} min (scene thresholds)")
    else:
        print(f"Camera:    {args.camera}  backend={args.backend}")
    print("Ctrl+C to stop.")

    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        agent.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
