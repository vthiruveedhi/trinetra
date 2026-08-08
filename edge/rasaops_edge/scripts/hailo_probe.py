#!/usr/bin/env python3
"""
Hailo-8L detection probe: prove the NPU detects people + reveal NMS output layout.

Runs yolov6n_h8l.hef on a test image (people) and prints the raw output structure,
then parses person detections and saves an annotated image.

Usage:
  /opt/rasaops/.venv/bin/python hailo_probe.py                 # uses bus.jpg (downloaded)
  /opt/rasaops/.venv/bin/python hailo_probe.py --img /path.jpg
  /opt/rasaops/.venv/bin/python hailo_probe.py --rtsp 'rtsp://...'   # one live frame
"""
import argparse, os, sys, time, urllib.request
import numpy as np

HEF_PATH = "/usr/share/hailo-models/yolov6n_h8l.hef"
TEST_IMG = "/opt/rasaops/bus.jpg"
TEST_URL = "https://ultralytics.com/images/bus.jpg"


def get_frame(args):
    import cv2
    if args.rtsp:
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = cv2.VideoCapture(args.rtsp, cv2.CAP_FFMPEG)
        frame = None
        for _ in range(10):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f; break
            time.sleep(0.3)
        cap.release()
        if frame is None:
            print("RTSP_GRAB_FAILED"); sys.exit(1)
        return frame
    path = args.img or TEST_IMG
    if not os.path.exists(path):
        print(f"downloading test image -> {path}")
        urllib.request.urlretrieve(TEST_URL, path)
    return cv2.imread(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=None)
    ap.add_argument("--rtsp", default=None)
    ap.add_argument("--out", default="/opt/rasaops/hailo_probe_out.jpg")
    args = ap.parse_args()

    import cv2
    from hailo_platform import (HEF, VDevice, ConfigureParams, HailoStreamInterface,
                                InferVStreams, InputVStreamParams, OutputVStreamParams,
                                FormatType)

    frame = get_frame(args)
    H0, W0 = frame.shape[:2]
    print(f"input frame: {W0}x{H0}")

    # Preprocess: resize to 640x640, BGR->RGB, uint8, add batch dim, force contiguous
    resized = cv2.resize(frame, (640, 640))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    batch = np.ascontiguousarray(rgb[np.newaxis, ...], dtype=np.uint8)  # (1,640,640,3)
    print(f"batch: shape={batch.shape} dtype={batch.dtype} nbytes={batch.nbytes} "
          f"contiguous={batch.flags['C_CONTIGUOUS']}")

    hef = HEF(HEF_PATH)
    in_info = hef.get_input_vstream_infos()[0]
    out_info = hef.get_output_vstream_infos()[0]
    in_name, out_name = in_info.name, out_info.name
    print(f"in_vstream:  {in_name}  (hef says shape {in_info.shape})")
    print(f"out_vstream: {out_name}")

    target = VDevice()
    cfg = ConfigureParams.create_from_hef(hef=hef, interface=HailoStreamInterface.PCIe)
    network_group = target.configure(hef, cfg)[0]
    ng_params = network_group.create_params()
    in_params = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
    out_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)

    # Use the exact input vstream name the configured network reports.
    try:
        cfg_in_infos = network_group.get_input_vstream_infos()
        cfg_in_name = cfg_in_infos[0].name
        print(f"configured input vstream name: {cfg_in_name}")
    except Exception as e:
        cfg_in_name = in_name
        print(f"(could not read configured vstream name: {e}; using hef name)")

    def try_infer(pipeline, key, arr, label):
        try:
            r = pipeline.infer({key: arr})
            print(f"  infer OK via {label}")
            return r
        except Exception as e:
            print(f"  infer FAILED via {label}: {type(e).__name__}: {str(e)[:120]}")
            return None

    t0 = time.time()
    results = None
    with InferVStreams(network_group, in_params, out_params) as pipeline:
        with network_group.activate(ng_params):
            # Strategy 1: exact configured name, (1,640,640,3)
            results = try_infer(pipeline, cfg_in_name, batch, "cfg_name + (1,H,W,C)")
            # Strategy 2: hef name, (1,640,640,3)
            if results is None and in_name != cfg_in_name:
                results = try_infer(pipeline, in_name, batch, "hef_name + (1,H,W,C)")
            # Strategy 3: no batch dim (H,W,C)
            if results is None:
                results = try_infer(pipeline, cfg_in_name,
                                    np.ascontiguousarray(rgb, dtype=np.uint8),
                                    "cfg_name + (H,W,C)")
    dt = (time.time() - t0) * 1000
    if results is None:
        print("ALL_INFER_STRATEGIES_FAILED"); sys.exit(1)
    print(f"inference: {dt:.0f} ms (includes activation)")

    out = results[out_name]
    print("\n=== RAW OUTPUT STRUCTURE ===")
    print("type:", type(out))
    if isinstance(out, np.ndarray):
        print("ndarray shape:", out.shape, "dtype:", out.dtype)
    elif isinstance(out, list):
        print("list len:", len(out))
        if out and isinstance(out[0], list):
            print("  [0] is list, len:", len(out[0]))
            nonempty = [(i, np.asarray(c).shape) for i, c in enumerate(out[0]) if len(np.asarray(c))]
            print("  non-empty classes in [0]:", nonempty[:10])
        elif out and isinstance(out[0], np.ndarray):
            print("  [0] ndarray shape:", out[0].shape)

    # ---- Parse: HAILO NMS BY CLASS. Try known layouts. ----
    # Layout A: list[batch] -> list[80 classes] -> (N,5) [ymin,xmin,ymax,xmax,score] normalized
    dets = []  # (class_id, score, x1,y1,x2,y2) in original-frame px
    def add(cls, ymin, xmin, ymax, xmax, score):
        dets.append((cls, float(score),
                     int(xmin * W0), int(ymin * H0), int(xmax * W0), int(ymax * H0)))

    parsed = False
    try:
        per_class = out[0] if isinstance(out, list) else out
        if isinstance(per_class, (list, np.ndarray)) and len(per_class) == 80:
            for cls_id, arr in enumerate(per_class):
                arr = np.asarray(arr)
                if arr.ndim == 2 and arr.shape[0] > 0 and arr.shape[1] >= 5:
                    for row in arr:
                        ymin, xmin, ymax, xmax, score = row[:5]
                        add(cls_id, ymin, xmin, ymax, xmax, score)
            parsed = True
    except Exception as e:
        print("parse layout A failed:", e)

    print(f"\nparsed={parsed}  total detections (all classes): {len(dets)}")
    persons = [d for d in dets if d[0] == 0]
    print(f"PERSON detections (class 0): {len(persons)}")
    for p in sorted(persons, key=lambda d: -d[1])[:10]:
        print(f"  person score={p[1]:.2f} box=({p[2]},{p[3]})-({p[4]},{p[5]})")

    # annotate
    for cls, score, x1, y1, x2, y2 in dets:
        color = (0, 255, 0) if cls == 0 else (0, 165, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"{cls}:{score:.2f}", (x1, max(0, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    cv2.imwrite(args.out, frame)
    print(f"\nannotated saved: {args.out}")
    print("PROBE_DONE")


if __name__ == "__main__":
    main()
