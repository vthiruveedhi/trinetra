# RasaOps on macOS

Run the same L1 lab stack (edge agent + kiosk + optional OpenCV preview) on Mac.

## Requirements

- macOS 12+ (Apple Silicon or Intel)
- Python 3.11 or 3.12 (`brew install python@3.12`)
- Optional: [Homebrew](https://brew.sh), `ffmpeg`
- Desktop session for live preview window (Camera permission for webcam)

## Quick start

```bash
cd /path/to/restaurant-ops-ai   # or this monorepo root

# One-time setup
bash scripts/mac/setup_mac.sh

# Demo (YouTube Coopers pub by default)
bash scripts/mac/start_demo.sh

# Stop
bash scripts/mac/stop_demo.sh
```

Open:

- Kiosk: http://127.0.0.1:8090/
- Cloud: http://127.0.0.1:18080/health

## Options

```bash
# Pub stream
bash scripts/mac/start_demo.sh \
  --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" \
  --scene pub_bar

# Kitchen scene
bash scripts/mac/start_demo.sh \
  --youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" \
  --scene kitchen_line

# Built-in webcam (grant Camera access when prompted)
bash scripts/mac/start_demo.sh --camera 0 --scene dining_restaurant

# Agent + kiosk only (no OpenCV window)
bash scripts/mac/start_demo.sh --no-preview

# Force Pi-like ONNX 320 (lower recall, lighter CPU)
RASAOPS_INFERENCE_BACKEND=onnx bash scripts/mac/start_demo.sh --no-preview
```

## Manual commands

```bash
source .venv/bin/activate

# Full stack
python -m rasaops_edge.scripts.run_edge_agent \
  --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" \
  --with-cloud --scene pub_bar --fps 1

# Live preview (separate terminal)
python -m rasaops_edge.scripts.run_live_view \
  --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" \
  --scene pub_bar --fps 3 --max-height 480

# Tests
pytest -q
```

## Vision backends on Mac

| Mode | Model | When |
|------|--------|------|
| **Host high-recall (default)** | Ultralytics `yolo11n.pt` @ 640 | `RASAOPS_HOST_HIGH_RECALL=1` and `.pt` present |
| **Pi-like** | ONNX `yolo11n_int8_320.onnx` | `RASAOPS_INFERENCE_BACKEND=onnx` |
| **Webcam fallback** | OpenCV HOG | No weights |

Large weights are gitignored. Place `edge/models/yolo11n.pt` (and/or ONNX) after:

```bash
# optional — export yourself
pip install ultralytics
yolo export model=yolo11n.pt format=onnx imgsz=320
# copy into edge/models/
```

Without weights, `auto` falls back to HOG/mock — fine for plumbing tests, not people count.

## Logs & PIDs

```
data/edge/logs/agent.log
data/edge/logs/preview.log
data/edge/pids/agent.pid
data/edge/pids/preview.pid
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Camera permission | System Settings → Privacy & Security → Camera → Terminal / iTerm / Python |
| No OpenCV window over SSH | Use `--no-preview` or run on the Mac desktop |
| YouTube bot check | Set cookies or use webcam; see `youtube_source.py` android client fallback |
| Slow on CPU | Lower `--preview-fps`, use `--no-preview`, or `RASAOPS_IMGSZ=320` |
| Port in use | `bash scripts/mac/stop_demo.sh` then retry |

## Cross-platform map

| Windows | macOS |
|---------|--------|
| `scripts/Start-RasaOps-Demo.ps1` | `scripts/mac/start_demo.sh` |
| `.venv\Scripts\python.exe` | `.venv/bin/python` |
| DirectShow webcam | AVFoundation webcam |
