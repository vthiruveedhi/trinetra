# Edge model pins (PR-04)

## Default product pin (KD-3)

| Field | Value |
|-------|--------|
| Artifact | `yolo11n_int8_320.onnx` |
| Input | 320×320 |
| Runtime | ONNX Runtime CPU (NCNN/Hailo later) |
| Keep classes (MVP) | `person` (+ optional `dining table`) |

`sha256` is recorded in `pin.json` after export.

## Generate CI tiny model

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
.\.venv\Scripts\python.exe edge\scripts\make_tiny_onnx.py
```

Produces `edge/models/tiny_yolo_like_320.onnx` — **not** accurate; validates ONNX load + postprocess.

## Export real YOLOv11n (optional, needs ultralytics)

```bash
pip install ultralytics
yolo export model=yolo11n.pt format=onnx imgsz=320 simplify=True
# Optionally quantize INT8 with onnxruntime quantization tools
# Copy to edge/models/yolo11n_int8_320.onnx and update pin.json sha256
```

**License:** Ultralytics AGPL may require enterprise license for closed SaaS — legal review before pilot.

## Without weights

```powershell
$env:RASAOPS_INFERENCE_BACKEND = "mock"
# or host dogfood pedestrian HOG (poor for seated desk cams):
$env:RASAOPS_INFERENCE_BACKEND = "hog"
```

`auto` prefers real YOLO ONNX if present, else HOG, else mock.

## Host webcam dogfood

```powershell
# After exporting yolo11n.onnx into edge/models/yolo11n_int8_320.onnx:
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0
# One-shot snapshot + events:
python edge/scripts/try_webcam_once.py 0
```

Desk zones: `shared/schemas/examples/zones.v1.desk_webcam.json` (large table-1 ROI for seated cams).
