#!/usr/bin/env python3
"""
trinetra Phase 1 — single MP4 in -> annotated MP4 + SQLite events out.

Usage:
    python analyze.py --input path/to/video.mp4 --config config.yaml --out output/

Outputs in --out:
    events.db                       SQLite with sessions / person_events / zone_dwells / line_crossings
    <stem>_annotated.mp4            Visual verification (boxes, IDs, traces, zones, line counters)
"""
import argparse
import json
import sqlite3
import time
import uuid
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml
from ultralytics import YOLO
import supervision as sv


# A track is considered to have left a zone if we don't see it in that zone
# for this many seconds. Avoids spurious exits from 1-frame detection misses
# and lets us correctly model "person walks through zone twice".
ZONE_EXIT_GRACE_SECONDS = 1.0


def load_config(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn.executescript(schema)
    return conn


def main() -> None:
    p = argparse.ArgumentParser(description="trinetra Phase 1 analyzer")
    p.add_argument("--input", required=True, help="Input video file (mp4, mov, etc.)")
    p.add_argument("--config", default="config.yaml", help="Zones/lines YAML")
    p.add_argument("--out", default="output", help="Output directory")
    p.add_argument("--model", default=None, help="Override config's model path")
    p.add_argument("--no-write-video", action="store_true",
                   help="Skip annotated video output (faster, smaller disk)")
    args = p.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(Path(args.config).expanduser().resolve())
    model_name = args.model or config.get("model", "yolo11n.pt")
    conf_threshold = float(config.get("confidence", 0.4))
    classes = config.get("classes", [0])

    # ---- Models / tracker ------------------------------------------------
    print(f"[load] model={model_name}")
    model = YOLO(model_name)

    # ---- Video probe -----------------------------------------------------
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / fps if fps else 0.0
    print(f"[video] {frame_w}x{frame_h} @ {fps:.1f}fps  ~{duration_s:.1f}s  {total_frames} frames")

    tracker = sv.ByteTrack(frame_rate=int(round(fps)))
    smoother = sv.DetectionsSmoother()

    # ---- Zones / lines ---------------------------------------------------
    zones: list[tuple[str, sv.PolygonZone]] = []
    for z in config.get("zones", []):
        polygon = np.array(z["polygon"], dtype=np.int32)
        zones.append((z["name"], sv.PolygonZone(polygon=polygon)))

    line_zones: list[tuple[str, sv.LineZone]] = []
    for ln in config.get("lines", []):
        start = sv.Point(*ln["from"])
        end = sv.Point(*ln["to"])
        line_zones.append((ln["name"], sv.LineZone(start=start, end=end)))

    # ---- Annotators ------------------------------------------------------
    box_ann = sv.BoxAnnotator(thickness=2)
    label_ann = sv.LabelAnnotator(text_scale=0.5, text_padding=4)
    trace_ann = sv.TraceAnnotator(thickness=2, trace_length=int(fps * 3))
    zone_anns = [
        (name, sv.PolygonZoneAnnotator(zone=z, color=sv.Color.GREEN, thickness=2))
        for name, z in zones
    ]
    line_ann = sv.LineZoneAnnotator(thickness=2, text_thickness=1, text_scale=0.5)

    # ---- DB session ------------------------------------------------------
    session_id = uuid.uuid4().hex[:12]
    db_path = out_dir / "events.db"
    conn = init_db(db_path)
    conn.execute(
        "INSERT INTO sessions (session_id, source_video, started_at, duration_seconds)"
        " VALUES (?, ?, ?, ?)",
        (session_id, str(input_path), time.strftime("%Y-%m-%d %H:%M:%S"), duration_s),
    )

    # ---- State -----------------------------------------------------------
    track_first_seen: dict[int, float] = {}
    track_last_seen: dict[int, float] = {}
    track_visible_frames: dict[int, int] = defaultdict(int)
    track_zones_visited: dict[int, set[str]] = defaultdict(set)

    # zone_state[tid][zone_name] = (entered_at, last_seen_in_zone_at) — open interval
    zone_state: dict[int, dict[str, tuple[float, float]]] = defaultdict(dict)
    completed_dwells: list[tuple] = []
    line_crossings: list[tuple] = []

    # ---- Video writer ----------------------------------------------------
    writer: cv2.VideoWriter | None = None
    if not args.no_write_video:
        out_video = out_dir / f"{input_path.stem}_annotated.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_video), fourcc, fps, (frame_w, frame_h))

    # ---- Frame loop ------------------------------------------------------
    frame_idx = 0
    last_report = time.time()
    print("[run] processing frames...")
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        t = frame_idx / fps

        results = model(frame, classes=classes, conf=conf_threshold, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(results)
        detections = tracker.update_with_detections(detections)
        detections = smoother.update_with_detections(detections)

        tids = (detections.tracker_id.tolist()
                if detections.tracker_id is not None else [])

        for tid in tids:
            if tid not in track_first_seen:
                track_first_seen[tid] = t
            track_last_seen[tid] = t
            track_visible_frames[tid] += 1

        # Zones: open or extend intervals; close after grace period
        for name, zone in zones:
            in_mask = zone.trigger(detections=detections)
            ids_in_zone: set[int] = (
                set(detections.tracker_id[in_mask].tolist())
                if detections.tracker_id is not None else set()
            )

            for tid in ids_in_zone:
                track_zones_visited[tid].add(name)
                if name not in zone_state[tid]:
                    zone_state[tid][name] = (t, t)
                else:
                    entered, _ = zone_state[tid][name]
                    zone_state[tid][name] = (entered, t)

            # Close intervals where the track has been out of this zone past grace
            for tid in list(zone_state.keys()):
                if name in zone_state[tid] and tid not in ids_in_zone:
                    entered, last_seen = zone_state[tid][name]
                    if t - last_seen > ZONE_EXIT_GRACE_SECONDS:
                        completed_dwells.append((
                            session_id, int(tid), name,
                            float(entered), float(last_seen),
                            float(last_seen - entered),
                        ))
                        del zone_state[tid][name]

        # Line crossings
        for name, line in line_zones:
            crossed_in, crossed_out = line.trigger(detections=detections)
            if detections.tracker_id is None:
                continue
            for direction, mask in (("in", crossed_in), ("out", crossed_out)):
                for tid in detections.tracker_id[mask].tolist():
                    line_crossings.append((
                        session_id, int(tid), name, direction, float(t),
                    ))

        # Annotate frame
        if writer is not None:
            scene = frame.copy()
            for _, ann in zone_anns:
                scene = ann.annotate(scene=scene)
            for _, line in line_zones:
                scene = line_ann.annotate(frame=scene, line_counter=line)
            scene = trace_ann.annotate(scene=scene, detections=detections)
            scene = box_ann.annotate(scene=scene, detections=detections)
            if detections.tracker_id is not None and detections.confidence is not None:
                labels = [
                    f"#{tid} person {conf:.2f}"
                    for tid, conf in zip(detections.tracker_id, detections.confidence)
                ]
            else:
                labels = []
            scene = label_ann.annotate(scene=scene, detections=detections, labels=labels)
            writer.write(scene)

        # Progress
        now = time.time()
        if now - last_report > 2.0:
            pct = (frame_idx / total_frames * 100) if total_frames else 0
            print(f"  frame {frame_idx}/{total_frames} ({pct:.1f}%)  active_tracks={len(set(tids))}")
            last_report = now

        frame_idx += 1

    cap.release()
    if writer is not None:
        writer.release()

    # Close any zone intervals still open at end of video
    for tid, zd in zone_state.items():
        for name, (entered, last_seen) in zd.items():
            completed_dwells.append((
                session_id, int(tid), name,
                float(entered), float(last_seen),
                float(last_seen - entered),
            ))

    # ---- Persist events --------------------------------------------------
    person_rows = []
    for tid, first in track_first_seen.items():
        last = track_last_seen[tid]
        visible_s = track_visible_frames[tid] / fps if fps else 0.0
        zones_visited = sorted(track_zones_visited[tid])
        person_rows.append((
            session_id, int(tid),
            float(first), float(last), float(visible_s),
            json.dumps(zones_visited),
        ))

    conn.executemany(
        "INSERT INTO person_events"
        " (session_id, tracker_id, first_seen_at, last_seen_at,"
        "  total_visible_seconds, zones_visited)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        person_rows,
    )
    conn.executemany(
        "INSERT INTO zone_dwells"
        " (session_id, tracker_id, zone_name, entered_at, exited_at, dwell_seconds)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        completed_dwells,
    )
    conn.executemany(
        "INSERT INTO line_crossings"
        " (session_id, tracker_id, line_name, direction, crossed_at)"
        " VALUES (?, ?, ?, ?, ?)",
        line_crossings,
    )
    conn.commit()

    # ---- Summary ---------------------------------------------------------
    print(f"\n[done] session={session_id}")
    print(f"  unique tracks         : {len(track_first_seen)}")
    print(f"  zone dwell intervals  : {len(completed_dwells)}")
    print(f"  line crossings        : {len(line_crossings)}")
    print(f"  events DB             : {db_path}")
    if writer is not None:
        print(f"  annotated video       : {out_dir / (input_path.stem + '_annotated.mp4')}")

    conn.close()


if __name__ == "__main__":
    main()
