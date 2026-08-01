"""
Live processing view: YouTube (or webcam) → YOLO → track → occupancy → OpenCV window.

  python -m rasaops_edge.scripts.run_live_view --youtube
  python -m rasaops_edge.scripts.run_live_view --camera 0
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "edge"))
sys.path.insert(0, str(_ROOT / "shared" / "python"))

DEFAULT_YT = "https://www.youtube.com/watch?v=0JGQo-vAgwQ"


def _letterbox(bgr: np.ndarray, tw: int, th: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    scale = min(tw / w, th / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(bgr, (nw, nh))
    canvas = np.zeros((th, tw, 3), dtype=np.uint8)
    px, py = (tw - nw) // 2, (th - nh) // 2
    canvas[py : py + nh, px : px + nw] = resized
    return canvas


def _draw(canvas, zones, tracks, metrics, scene, title: str) -> np.ndarray:
    out = canvas.copy()
    colors = {
        "table": (0, 200, 0),
        "entrance": (255, 180, 0),
        "queue": (0, 165, 255),
        "pass": (200, 100, 255),
        "other": (180, 180, 180),
    }
    for z in zones.zones:
        pts = np.array(z.polygon, dtype=np.int32)
        color = colors.get(z.kind, (200, 200, 200))
        cv2.polylines(out, [pts], True, color, 2)
        x, y = int(pts[0][0]), int(pts[0][1])
        cv2.putText(
            out,
            z.label or z.zone_id,
            (x + 4, y + 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )
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
    tables = metrics.get("tables") or []
    table_s = " | ".join(
        f"{t['zone_id']}={(t.get('state') or '?')[0]}/{t.get('person_count_est', 0)}"
        for t in tables
    )
    sc = scene or {}
    vibe = sc.get("vibe") or {}
    atmo = (vibe.get("atmosphere") or {})
    mov = (vibe.get("movement") or {})
    drinks = (vibe.get("drinks") or {})
    eat = (vibe.get("eating") or {})
    lines = [
        (title[:70] if title else "RasaOps live"),
        f"scene={sc.get('scene_type', '—')}  people={sc.get('person_count', metrics.get('track_count', 0))}  "
        f"load={sc.get('floor_load', '—')}  infer={metrics.get('inference_ms', 0):.0f}ms",
        f"HAPPINESS~{sc.get('happiness_index', vibe.get('happiness_index', '—'))}  "
        f"ATMO={sc.get('atmosphere_label', atmo.get('label', '—'))}  "
        f"MOVE={sc.get('movement_label', mov.get('movement_label', '—'))}",
        f"drinks_vis={sc.get('drinks_visible', drinks.get('visible_now', 0))}  "
        f"eating={sc.get('eating_label', eat.get('label', '—'))}  "
        f"speed={mov.get('mean_person_speed_px_s', '—')}px/s",
        f"tables: {table_s}",
    ]
    pub = sc.get("pub") or {}
    if pub:
        lines.append(
            f"PUB bar={pub.get('bar_pressure', '—')} energy={pub.get('social_energy', '—')} "
            f"bar_ppl≈{pub.get('bar_crowd_est', 0)}  {str(pub.get('service_hint', ''))[:48]}"
        )
    kit = sc.get("kitchen") or {}
    if kit:
        lines.append(
            f"KITCHEN cook={kit.get('cooking_load', '—')} plate={kit.get('plating_pressure', '—')} "
            f"pace={kit.get('line_pace', '—')} bal={kit.get('line_balance_score', '—')}"
        )
        lines.append(f"hint: {str(kit.get('line_hint', ''))[:72]}")
    lines.append("proxy metrics only (no face emotion)  |  q/ESC quit")
    y0 = 18
    for line in lines:
        cv2.putText(
            out, line, (8, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 20, 20), 3, cv2.LINE_AA
        )
        cv2.putText(
            out, line, (8, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1, cv2.LINE_AA
        )
        y0 += 18
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="RasaOps live processing view")
    p.add_argument("--camera", type=int, default=None)
    p.add_argument(
        "--youtube",
        nargs="?",
        const=DEFAULT_YT,
        default=None,
        help="YouTube URL (default Lyn's Sisig live if flag alone)",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=float(os.environ.get("RASAOPS_LIVE_FPS", "5")),
        help="Target process FPS (default 5 for snappier preview)",
    )
    p.add_argument("--backend", default=os.environ.get("RASAOPS_INFERENCE_BACKEND", "auto"))
    p.add_argument("--zones", default=None)
    p.add_argument(
        "--scene",
        default="auto",
        choices=["dining_restaurant", "pub_bar", "kitchen_line", "auto"],
    )
    p.add_argument(
        "--max-height",
        type=int,
        default=int(os.environ.get("RASAOPS_LIVE_MAX_HEIGHT", "480")),
        help="YouTube decode height cap (lower = less lag; default 480)",
    )
    p.add_argument(
        "--no-atmosphere",
        action="store_true",
        help="Person-only classes (slightly less post-process work)",
    )
    p.add_argument(
        "--high-priority",
        action="store_true",
        default=True,
        help="Raise process priority (Windows HIGH_PRIORITY / Unix nice)",
    )
    args = p.parse_args(argv)

    # Prefer more ORT threads for live preview unless user already set it
    os.environ.setdefault("RASAOPS_ORT_THREADS", str(max(8, (os.cpu_count() or 8) - 4)))

    # Default to youtube if nothing specified
    if args.youtube is None and args.camera is None:
        args.youtube = DEFAULT_YT

    from rasaops_edge.capture.webcam_source import WebcamSource
    from rasaops_edge.capture.youtube_source import YoutubeSource
    from rasaops_edge.inference.factory import create_backend
    from rasaops_edge.metrics.scene_metrics import SceneMetricsEngine, config_for_scene
    from rasaops_edge.pipeline import EdgePipeline
    from rasaops_shared.zones import load_zones

    use_yt = bool(args.youtube)
    yt = (args.youtube or "").lower()
    scene = args.scene
    if scene == "auto":
        if "0jgqo-vagwq" in yt or "cooper" in yt:
            scene = "pub_bar"
        elif (
            "sfgiattj9lc" in yt
            or "wscyqjh_5tu" in yt
            or "8zq8e13yhbk" in yt
            or "grill" in yt
            or "kitchen" in yt
            or "pov" in yt
            or "chef" in yt
        ):
            scene = "kitchen_line"
        else:
            scene = "dining_restaurant"

    if args.zones:
        zones_path = args.zones
    elif scene == "pub_bar":
        zones_path = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.pub_live.json"
        )
    elif scene == "kitchen_line":
        zones_path = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.kitchen_live.json"
        )
    elif use_yt:
        zones_path = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.restaurant_live.json"
        )
    else:
        zones_path = str(
            _ROOT / "shared" / "schemas" / "examples" / "zones.v1.desk_webcam.json"
        )

    if args.high_priority:
        try:
            import sys

            if os.name == "nt":
                import ctypes

                # HIGH_PRIORITY_CLASS = 0x00000080
                ctypes.windll.kernel32.SetPriorityClass(
                    ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080
                )
                print("Process priority: HIGH (Windows)")
            else:
                # Lower nice value = higher priority (may need privileges on some systems)
                try:
                    os.nice(-5)
                    print("Process priority: nice -5 (Unix/macOS)")
                except PermissionError:
                    # Best-effort without root
                    print("Process priority: default (nice not permitted without privileges)")
        except Exception as exc:
            print(f"Could not raise process priority: {exc}")

    zones = load_zones(zones_path)
    tw, th = int(zones.image_width or 640), int(zones.image_height or 480)
    use_atmo = (not args.no_atmosphere) and scene in (
        "pub_bar",
        "dining_restaurant",
        "kitchen_line",
    )
    backend = create_backend(backend=args.backend, atmosphere=use_atmo)
    pipe = EdgePipeline.from_paths(
        zones_path,
        backend=backend,
        device_id="lab-live-view",
        frames_upload_entitled=False,
    )
    pipe.config.t_enter_sec = 2.0
    pipe.config.t_leave_sec = 4.0
    pipe.config.sample_fps = args.fps
    pipe.occupancy.config = pipe.config

    scene_eng = SceneMetricsEngine(
        config=config_for_scene(scene, improve_interval_sec=20 * 60),
        state_path=_ROOT / "data" / "edge" / f"scene_metrics_{scene}.json",
    )

    if use_yt:
        src = YoutubeSource(
            page_url=args.youtube,
            target_fps=args.fps,
            max_height=args.max_height,
            low_latency=True,
            buffer_drain=6,
        )
        title = "YouTube live"
    else:
        src = WebcamSource(device=args.camera, target_fps=args.fps, width=tw, height=th)
        title = f"Webcam {args.camera}"

    print(f"Opening {title}…")
    src.open()
    if use_yt:
        title = src.title
    print(f"Source OK: {title}")
    print(
        f"Live perf: fps={args.fps} max_h={args.max_height} "
        f"ort_threads={os.environ.get('RASAOPS_ORT_THREADS')} "
        f"atmosphere={use_atmo} low_latency=on"
    )
    print("Window: RasaOps live processing  |  q / ESC to quit")

    win = "RasaOps live processing"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, 960, 720)

    try:
        for frame in src.frames():
            if frame.bgr is None:
                continue
            # Fast resize into zone canvas
            canvas = _letterbox(frame.bgr, tw, th)
            result = pipe.process_bgr(canvas, timestamp_ms=frame.timestamp_ms, scrub=False)
            # Skip heavy frame_bgr atmosphere motion every other tick when lagging
            scene_out = scene_eng.update(
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
            for ev in result.events:
                print(
                    f"[event] {ev.event_type.value} "
                    f"zone={ev.zone.zone_id if ev.zone else None} "
                    f"load={scene_out.get('floor_load')}"
                )
            kit = scene_out.get("kitchen") or {}
            if kit:
                # Keep overlay kitchen-aware without extra work
                pass
            vis = _draw(canvas, zones, result.tracks, result.metrics, scene_out, title)
            # Faster upscale (NEAREST is fine for live dogfood)
            vis2 = cv2.resize(vis, (960, 720), interpolation=cv2.INTER_NEAREST)
            cv2.imshow(win, vis2)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        src.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
