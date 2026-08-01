"""One-shot webcam + YOLO dogfood; saves annotated snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_ROOT / "edge"), str(_ROOT / "shared" / "python")]

from rasaops_edge.capture.webcam_source import WebcamSource
from rasaops_edge.inference.onnx_yolo import OnnxYoloBackend
from rasaops_edge.pipeline import EdgePipeline


def main() -> int:
    cam = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    zones = _ROOT / "shared" / "schemas" / "examples" / "zones.v1.desk_webcam.json"
    model = _ROOT / "edge" / "models" / "yolo11n_int8_320.onnx"
    out = _ROOT / "devops" / "virtual-pi" / "fixtures" / f"webcam_cam{cam}_yolo_snapshot.jpg"

    print(f"model={model.exists()} size={model.stat().st_size if model.exists() else 0}")
    be = OnnxYoloBackend(model, input_size=320, conf_thres=0.25)
    print(f"backend={be.model_version}")

    src = WebcamSource(device=cam, target_fps=2.0, width=640, height=480)
    src.open()
    for _ in range(8):
        src.read()  # AE settle

    pipe = EdgePipeline.from_paths(zones, backend=be, device_id=f"lab-cam-{cam}")
    pipe.config.t_enter_sec = 1.0
    pipe.config.sample_fps = 2.0
    pipe.occupancy.config = pipe.config

    events: list[str] = []
    best = None
    best_n = -1
    for n in range(16):
        fr = src.read()
        if fr is None or fr.bgr is None:
            print(f"frame {n}: read failed")
            continue
        bgr = fr.bgr
        h, w = bgr.shape[:2]
        tw, th = 640, 480
        scale = min(tw / w, th / h)
        nw, nh = int(w * scale), int(h * scale)
        resized = cv2.resize(bgr, (nw, nh))
        canvas = np.zeros((th, tw, 3), dtype=np.uint8)
        px, py = (tw - nw) // 2, (th - nh) // 2
        canvas[py : py + nh, px : px + nw] = resized

        r = pipe.process_bgr(canvas, fr.timestamp_ms)
        nd = len(r.detections)
        boxes = [
            (round(d.conf, 2), d.class_name, tuple(int(x) for x in d.xyxy))
            for d in r.detections[:5]
        ]
        ev = [e.event_type.value for e in r.events]
        events.extend(ev)
        print(f"frame {n}: {w}x{h} dets={nd} tracks={len(r.tracks)} ms={r.inference_ms:.0f} {boxes} {ev}")

        if nd >= best_n:
            best_n = nd
            vis = canvas.copy()
            for z in pipe.zones.zones:
                pts = np.array(z.polygon, dtype=np.int32)
                cv2.polylines(vis, [pts], True, (0, 200, 0), 2)
            for d in r.detections:
                x1, y1, x2, y2 = (int(v) for v in d.xyxy)
                cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 255), 2)
                cv2.putText(
                    vis,
                    f"{d.class_name} {d.conf:.2f}",
                    (x1, max(16, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 255),
                    1,
                )
            cv2.putText(
                vis,
                f"YOLO cam={cam} dets={nd}",
                (8, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            best = vis

    src.close()
    if best is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out), best)
        print(f"saved {out}")
    print(f"events={events}")
    return 0 if best_n > 0 or "table_occupied" in events else 1


if __name__ == "__main__":
    raise SystemExit(main())
