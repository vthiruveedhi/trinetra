# Phase 1 — single video → per-person events

Process one MP4, output an annotated MP4 + SQLite events. No live cameras, no re-ID, no demographics yet — that's Phase 2 / 3 / later.

## What it produces

For each tracked person:
- **`person_events`** — tracker_id, first/last seen (sec into video), total visible seconds, zones visited
- **`zone_dwells`** — per (tracker_id, zone) entry / exit / dwell-seconds, with re-entry support (grace period = 1.0s)
- **`line_crossings`** — per (tracker_id, line) direction-of-crossing

Plus an annotated MP4 with boxes, track IDs, recent trajectory, zone outlines, and line-crossing counters drawn on every frame.

## Setup

```bash
cd scripts/phase1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

First run downloads the YOLOv11n weights (~5 MB) automatically into the current directory.

## Run

```bash
# Copy the example config and tune coordinates for your video
cp config.example.yaml config.yaml

# Process a video
python analyze.py --input ~/Videos/restaurant.mp4 --config config.yaml --out output/
```

Useful flags:
- `--no-write-video` — skip annotated video output (faster, smaller disk)
- `--model yolo11s.pt` — bigger model (slower, more accurate). Auto-downloads.

Outputs land in `output/`:
- `events.db` — SQLite, queryable below
- `<videoname>_annotated.mp4` — visual sanity check

## Configure zones & lines

Edit `config.yaml`. Coordinates are **pixel (x, y) in the source video's resolution**. Easiest workflow:

1. Open the first frame in Preview / any image viewer.
2. Hover over the corners of your zones (entry area, counter area, queue area...).
3. Note the (x, y), drop into the YAML.

```yaml
zones:
  - name: entry
    polygon:
      - [100, 400]
      - [500, 400]
      - [500, 700]
      - [100, 700]

lines:
  - name: door
    from: [200, 100]
    to:   [200, 700]
```

`from` / `to` define direction: crossing from left of the line to right is "in"; the reverse is "out". Flip the endpoints to flip the convention.

## Inspect the events

```bash
sqlite3 output/events.db

> SELECT tracker_id, ROUND(first_seen_at,1) AS in_at,
>        ROUND(total_visible_seconds,1) AS visible_s, zones_visited
>   FROM person_events ORDER BY first_seen_at;

> SELECT tracker_id, zone_name, ROUND(dwell_seconds,1) AS dwell_s
>   FROM zone_dwells ORDER BY dwell_s DESC LIMIT 20;

> SELECT line_name, direction, COUNT(*) AS n
>   FROM line_crossings GROUP BY line_name, direction;
```

## Known limitations (Phase 1)

- **No re-identification across track losses.** If someone is occluded long enough that ByteTrack drops the track, they'll get a new ID when they reappear. Re-ID lands in Phase 3 (OSNet + pgvector).
- **No demographics.** Deferred — InsightFace weights are research-only, and ethnicity detection is a regulatory landmine (EU AI Act + state laws). Anonymous age bands will land later with a permissively-licensed model.
- **Batch only.** Live RTSP arrives in Phase 2 via Frigate.
- **Single video.** Multi-camera with cross-camera tracking arrives in Phase 3.
- **CPU on Mac runs at ~3–8 fps for YOLOv11n on 1080p.** Acceptable for Phase 1. Cloud GPU comes in Phase 3.

## Where to get test footage (if you don't have a clip yet)

- **MOT challenge** — https://motchallenge.net/data/MOT17/ — free person-tracking benchmark videos
- **Pexels** — https://www.pexels.com/search/videos/restaurant/ — CC-licensed restaurant interior clips
- Your phone camera pointed at any room with people
