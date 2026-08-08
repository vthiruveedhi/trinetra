#!/usr/bin/env python3
"""
trinetra — live person counter on the Hailo-8L NPU.

RUN UNDER SYSTEM python3 (numpy 1.x + cv2 + hailo_platform), NOT the venv:
    RTSP_URL='rtsp://user:pass@192.168.68.55:554/live/ch0' \
      python3 edge/rasaops_edge/scripts/hailo_count.py

Then open http://<pi-ip>:8090 in a browser: live annotated video with boxes,
current person count, session peak, and unique-appearances tally.

Pipeline: RTSP grab -> yolov6n_h8l.hef on NPU (~15ms) -> person boxes ->
lightweight IoU tracker -> MJPEG + JSON over stdlib http.server.
No torch, no venv, single RTSP connection.
"""
import json
import os
import threading
import time

import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hailo_platform import (HEF, VDevice, ConfigureParams, HailoStreamInterface,
                            InferVStreams, InputVStreamParams, OutputVStreamParams,
                            FormatType)

HEF_PATH = "/usr/share/hailo-models/yolov6n_h8l.hef"
RTSP_URL = os.environ.get("RTSP_URL",
                          "rtsp://cO1ErY4y:XzMU6lh3g5wIUVxs@192.168.68.55:554/live/ch0")
PORT = int(os.environ.get("PORT", "8090"))
PERSON_CLASS = 0
SCORE_TH = float(os.environ.get("SCORE_TH", "0.4"))

STATE = {"jpeg": None, "count": 0, "peak": 0, "unique": 0, "fps": 0.0}
LOCK = threading.Lock()


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


class SimpleTracker:
    """Minimal IoU tracker → assigns stable IDs, counts unique appearances."""
    def __init__(self, iou_th=0.3, max_missed=15):
        self.iou_th = iou_th
        self.max_missed = max_missed
        self.tracks = {}          # id -> {"box":(x1,y1,x2,y2), "missed":int}
        self.next_id = 1

    def update(self, boxes):
        for t in self.tracks.values():
            t["matched"] = False
        for box in boxes:
            best_id, best_iou = None, self.iou_th
            for tid, t in self.tracks.items():
                if t["matched"]:
                    continue
                i = iou(t["box"], box)
                if i > best_iou:
                    best_iou, best_id = i, tid
            if best_id is not None:
                self.tracks[best_id].update(box=box, missed=0, matched=True)
            else:
                self.tracks[self.next_id] = {"box": box, "missed": 0, "matched": True}
                self.next_id += 1
        # age out unmatched
        for tid in list(self.tracks):
            if not self.tracks[tid]["matched"]:
                self.tracks[tid]["missed"] += 1
                if self.tracks[tid]["missed"] > self.max_missed:
                    del self.tracks[tid]
        active = [(tid, t["box"]) for tid, t in self.tracks.items() if t["missed"] == 0]
        return active, self.next_id - 1  # active tracks, total unique ever seen


class HailoPersonDetector:
    def __init__(self):
        self.hef = HEF(HEF_PATH)
        self.in_name = self.hef.get_input_vstream_infos()[0].name
        self.out_name = self.hef.get_output_vstream_infos()[0].name
        self.target = VDevice()
        cfg = ConfigureParams.create_from_hef(hef=self.hef,
                                              interface=HailoStreamInterface.PCIe)
        self.ng = self.target.configure(self.hef, cfg)[0]
        self.ng_params = self.ng.create_params()
        self.in_params = InputVStreamParams.make(self.ng, format_type=FormatType.UINT8)
        self.out_params = OutputVStreamParams.make(self.ng, format_type=FormatType.FLOAT32)
        # keep network activated + pipeline open for the whole run
        self._act = self.ng.activate(self.ng_params)
        self._act.__enter__()
        self._pipe_ctx = InferVStreams(self.ng, self.in_params, self.out_params)
        self._pipe = self._pipe_ctx.__enter__()

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        resized = cv2.resize(frame_bgr, (640, 640))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        batch = np.ascontiguousarray(rgb[np.newaxis, ...], dtype=np.uint8)
        res = self._pipe.infer({self.in_name: batch})
        out = res[self.out_name]
        per_class = out[0]
        arr = np.asarray(per_class[PERSON_CLASS])
        boxes = []
        if arr.ndim == 2:
            for row in arr:
                ymin, xmin, ymax, xmax, score = row[:5]
                if score >= SCORE_TH:
                    boxes.append((int(xmin * w), int(ymin * h),
                                  int(xmax * w), int(ymax * h)))
        return boxes


def detect_loop():
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    det = HailoPersonDetector()
    tracker = SimpleTracker()
    print(f"[hailo] model loaded, opening RTSP {RTSP_URL.split('@')[-1]}")
    cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    peak = 0
    t_prev = time.time()
    fail = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            fail += 1
            if fail % 20 == 0:
                print(f"[rtsp] read failing ({fail}), reopening")
                cap.release()
                cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
            time.sleep(0.05)
            continue
        fail = 0

        boxes = det.detect(frame)
        active, unique = tracker.update(boxes)
        count = len(active)
        peak = max(peak, count)

        now = time.time()
        fps = 1.0 / max(1e-3, now - t_prev)
        t_prev = now

        for tid, (x1, y1, x2, y2) in active:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 220, 0), 3)
            cv2.putText(frame, f"#{tid}", (x1, max(0, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 220, 0), 2)
        banner = f"people now: {count}   peak: {peak}   unique: {unique}   {fps:4.1f} fps"
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), (0, 0, 0), -1)
        cv2.putText(frame, banner, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (255, 255, 255), 2)

        ok_enc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok_enc:
            with LOCK:
                STATE["jpeg"] = buf.tobytes()
                STATE["count"] = count
                STATE["peak"] = peak
                STATE["unique"] = unique
                STATE["fps"] = round(fps, 1)


PAGE = """<!doctype html><html><head><title>trinetra live</title>
<style>body{background:#111;color:#eee;font-family:system-ui;text-align:center;margin:0;padding:12px}
img{max-width:100%;border:1px solid #333;border-radius:8px}
#c{font-size:20px;margin:10px;font-variant-numeric:tabular-nums}</style></head>
<body><h2>trinetra — Hailo-8L live person count</h2>
<div id=c>connecting...</div><img src='/stream'>
<script>setInterval(async()=>{try{let j=await (await fetch('/count')).json();
document.getElementById('c').innerText=`people now: ${j.count}   peak: ${j.peak}   unique: ${j.unique}   ${j.fps} fps`;}catch(e){}},500)</script>
</body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)
        elif self.path == "/count":
            with LOCK:
                body = json.dumps({k: STATE[k] for k in ("count", "peak", "unique", "fps")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with LOCK:
                        jpg = STATE["jpeg"]
                    if jpg is not None:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.08)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()


def main():
    t = threading.Thread(target=detect_loop, name="detect", daemon=True)
    t.start()
    print(f"[web] open http://0.0.0.0:{PORT}/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
