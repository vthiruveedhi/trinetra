# RasaOps Edge

Runs on Raspberry Pi 5 (production) or host / Virtual Pi (dev).

## L1 dogfood smoke (Windows)

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,edge]"
pytest -q
python -m rasaops_edge.scripts.smoke_capture --frames 3
python -m rasaops_edge.scripts.run_pipeline_smoke --frames 12
```

## Live USB webcam (host PC, before Pi)

Uses your PC webcam. Without product YOLO weights, **auto** picks OpenCV **HOG** person detection (lab only).

```powershell
# List cameras
python -m rasaops_edge.scripts.run_webcam_dogfood --list-cameras

# Live window: walk into green Table-1 box for ~2s → table_occupied
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0 --fps 2

# With privacy scrub stamp (head-ROI until real face model)
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0 --scrub

# Force backends
$env:RASAOPS_INFERENCE_BACKEND = "hog"   # OpenCV people detector
$env:RASAOPS_INFERENCE_BACKEND = "onnx"  # needs edge/models/yolo11n_int8_320.onnx
```

Preview keys: **q** / **Esc** quit. Sample zones assume 640×480; frame is letterboxed to that canvas.

### PR-04 / PR-05

| Command | What |
|---------|------|
| `python edge/scripts/make_tiny_onnx.py` | CI tiny ONNX for load path |
| `python edge/scripts/bench_edge_inference.py` | Inference latency bench |
| `python -m rasaops_edge.scripts.run_pipeline_smoke` | Mock dets → occupy event |
| `$env:RASAOPS_INFERENCE_BACKEND='mock'` | Force mock detector |

Optional real YOLO weights: see `edge/models/README.md`.

Optional OpenCV + test video:

```powershell
.\devops\virtual-pi\Generate-TestPattern.ps1
$env:RASAOPS_VIDEO_PATH = "C:\Users\tvikr\restaurant-ops-ai\devops\virtual-pi\fixtures\sample_dining.mp4"
python -m rasaops_edge.scripts.smoke_capture
```

## Packages

| Path | Role |
|------|------|
| `rasaops_edge/capture` | CSI / RTSP / **file** sources |
| `rasaops_edge/inference` | ONNX YOLO11n + mock (PR-04) |
| `rasaops_edge/tracking` | ByteTrack-lite + polygons (PR-05) |
| `rasaops_edge/events` | Occupancy / queue / entry SM (PR-05) |
| `rasaops_edge/pipeline` | Capture→infer→track→events→optional scrub |
| `rasaops_edge/privacy` | Fail-closed scrubber (PR-06) — decision table + head-ROI |
| `rasaops_edge/queue` | SQLite offline queue (PR-07) |

### PR-06 privacy smoke

```powershell
pytest edge/tests/test_privacy_scrubber.py -q
# Optional: scrub on a pipeline frame
python -c "
from rasaops_edge.privacy import PrivacyScrubber, PrivacyConfig, MockFaceDetector, FaceBox
import numpy as np
s = PrivacyScrubber(config=PrivacyConfig(frames_upload_entitled=True),
                    face_detector=MockFaceDetector(faces=[FaceBox((10,10,40,40),0.9)]))
r = s.scrub(np.zeros((120,160,3),dtype=np.uint8), [(5,5,50,100)])
print(r.redaction_status, r.frame_upload_allowed)
"
```
| `rasaops_edge/dashboard_api` | Local FastAPI (PR-08) |
