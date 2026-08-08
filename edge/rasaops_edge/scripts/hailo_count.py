#!/usr/bin/env python3
"""
trinetra — live person counter on the Hailo-8L NPU, with a real dashboard.

RUN UNDER SYSTEM python3 (numpy 1.x + cv2 + hailo_platform), NOT the venv:

    # garage RTSP camera:
    SOURCE_URL='rtsp://user:pass@192.168.68.55:554/live/ch0' \
      python3 edge/rasaops_edge/scripts/hailo_count.py

    # a public YouTube live stream (resolved via the venv's yt-dlp):
    SOURCE_URL='https://www.youtube.com/watch?v=0JGQo-vAgwQ' \
      python3 edge/rasaops_edge/scripts/hailo_count.py

Then open http://<pi-ip>:8090 — live annotated video + current/peak/unique
counts + a rolling sparkline of occupancy.

Pipeline: RTSP|YouTube -> yolov6n_h8l.hef on NPU (~15ms) -> person boxes ->
IoU tracker -> MJPEG + JSON over stdlib http.server. No torch, no venv deps.
"""
import collections
import json
import os
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hailo_platform import (HEF, VDevice, ConfigureParams, HailoStreamInterface,
                            InferVStreams, InputVStreamParams, OutputVStreamParams,
                            FormatType)

HEF_PATH = "/usr/share/hailo-models/yolov6n_h8l.hef"
SOURCE_URL = (os.environ.get("SOURCE_URL") or os.environ.get("RTSP_URL")
              or "rtsp://cO1ErY4y:XzMU6lh3g5wIUVxs@192.168.68.55:554/live/ch0")
PORT = int(os.environ.get("PORT", "8090"))
PERSON_CLASS = 0
SCORE_TH = float(os.environ.get("SCORE_TH", "0.4"))
YT_MAX_H = int(os.environ.get("YT_MAX_HEIGHT", "720"))
YT_REFRESH_SEC = 300.0

STATE = {"jpeg": None, "count": 0, "peak": 0, "unique": 0, "fps": 0.0,
         "source": "", "live": False, "started": time.time(),
         "history": collections.deque(maxlen=120)}
LOCK = threading.Lock()


# ----------------------------- source -----------------------------
def _yt_dlp_bin():
    for c in (os.environ.get("YT_DLP_BIN"), "/opt/rasaops/.venv/bin/yt-dlp",
              shutil.which("yt-dlp")):
        if c and os.path.exists(c):
            return c
    return None


def resolve_youtube(page_url):
    """Resolve a YouTube URL to a direct media URL via the venv's yt-dlp."""
    binp = _yt_dlp_bin()
    fmt = (f"best[height<={YT_MAX_H}][ext=mp4][protocol^=http]/"
           f"best[height<={YT_MAX_H}][ext=mp4]/best[height<={YT_MAX_H}]/best[ext=mp4]/best")
    base = ["-g", "-f", fmt, "--no-warnings", "--quiet", "--no-playlist"]
    client_variants = ["youtube:player_client=android,ios",
                       "youtube:player_client=tv_embedded,mweb", None]
    if not binp:
        raise RuntimeError("yt-dlp not found (looked in venv + PATH). Set YT_DLP_BIN.")
    last = None
    for ea in client_variants:
        cmd = [binp, *base]
        if ea:
            cmd += ["--extractor-args", ea]
        cmd.append(page_url)
        try:
            out = subprocess.check_output(cmd, text=True, timeout=60,
                                          stderr=subprocess.STDOUT).strip().splitlines()
            if out and out[0].startswith("http"):
                return out[0]  # first line = video URL
        except Exception as e:
            last = e
    raise RuntimeError(f"yt-dlp could not resolve {page_url}: {last}")


class Source:
    def __init__(self, url):
        self.url = url
        self.is_yt = ("youtube.com" in url) or ("youtu.be" in url)
        self.cap = None
        self.opened_at = 0.0
        if self.is_yt:
            self.label = "YouTube live"
        else:
            self.label = "RTSP " + url.split("@")[-1]

    def open(self):
        self.close()
        if self.is_yt:
            direct = resolve_youtube(self.url)
            target = direct
        else:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            target = self.url
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if not cap.isOpened():
            raise RuntimeError(f"could not open source: {self.label}")
        self.cap = cap
        self.opened_at = time.time()

    def read(self):
        if self.cap is None:
            self.open()
        if self.is_yt and time.time() - self.opened_at > YT_REFRESH_SEC:
            try:
                self.open()  # HLS tokens expire
            except Exception:
                pass
        ok, frame = self.cap.read()
        return frame if (ok and frame is not None and frame.size) else None

    def close(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


# ----------------------------- tracker -----------------------------
def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1, ix2, iy2 = max(ax1, bx1), max(ay1, by1), min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    return inter / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)


class SimpleTracker:
    def __init__(self, iou_th=0.3, max_missed=15):
        self.iou_th, self.max_missed = iou_th, max_missed
        self.tracks, self.next_id = {}, 1

    def update(self, boxes):
        for t in self.tracks.values():
            t["matched"] = False
        for box in boxes:
            best_id, best = None, self.iou_th
            for tid, t in self.tracks.items():
                if t["matched"]:
                    continue
                i = iou(t["box"], box)
                if i > best:
                    best, best_id = i, tid
            if best_id is not None:
                self.tracks[best_id].update(box=box, missed=0, matched=True)
            else:
                self.tracks[self.next_id] = {"box": box, "missed": 0, "matched": True}
                self.next_id += 1
        for tid in list(self.tracks):
            if not self.tracks[tid]["matched"]:
                self.tracks[tid]["missed"] += 1
                if self.tracks[tid]["missed"] > self.max_missed:
                    del self.tracks[tid]
        active = [(tid, t["box"]) for tid, t in self.tracks.items() if t["missed"] == 0]
        return active, self.next_id - 1


# ----------------------------- detector -----------------------------
class HailoPersonDetector:
    def __init__(self):
        self.hef = HEF(HEF_PATH)
        self.in_name = self.hef.get_input_vstream_infos()[0].name
        self.out_name = self.hef.get_output_vstream_infos()[0].name
        self.target = VDevice()
        cfg = ConfigureParams.create_from_hef(hef=self.hef, interface=HailoStreamInterface.PCIe)
        self.ng = self.target.configure(self.hef, cfg)[0]
        self.ng_params = self.ng.create_params()
        self.in_params = InputVStreamParams.make(self.ng, format_type=FormatType.UINT8)
        self.out_params = OutputVStreamParams.make(self.ng, format_type=FormatType.FLOAT32)
        self._act = self.ng.activate(self.ng_params)
        self._act.__enter__()
        self._pipe_ctx = InferVStreams(self.ng, self.in_params, self.out_params)
        self._pipe = self._pipe_ctx.__enter__()

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame_bgr, (640, 640)), cv2.COLOR_BGR2RGB)
        batch = np.ascontiguousarray(rgb[np.newaxis, ...], dtype=np.uint8)
        out = self._pipe.infer({self.in_name: batch})[self.out_name]
        arr = np.asarray(out[0][PERSON_CLASS])
        boxes = []
        if arr.ndim == 2:
            for row in arr:
                ymin, xmin, ymax, xmax, score = row[:5]
                if score >= SCORE_TH:
                    boxes.append((int(xmin * w), int(ymin * h), int(xmax * w), int(ymax * h)))
        return boxes


def detect_loop():
    det = HailoPersonDetector()
    tracker = SimpleTracker()
    src = Source(SOURCE_URL)
    with LOCK:
        STATE["source"] = src.label
    print(f"[hailo] model loaded. source = {src.label}")
    peak, t_prev, fail = 0, time.time(), 0
    while True:
        try:
            frame = src.read()
        except Exception as e:
            print(f"[source] {e}; retrying in 3s")
            time.sleep(3)
            continue
        if frame is None:
            fail += 1
            if fail % 20 == 0:
                print(f"[source] read failing ({fail}), reopening")
                try:
                    src.open()
                except Exception as e:
                    print(f"[source] reopen failed: {e}")
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
            cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 220, 90), 3)
            cv2.putText(frame, f"#{tid}", (x1, max(12, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 220, 90), 2)
        ok_enc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if ok_enc:
            with LOCK:
                STATE.update(jpeg=buf.tobytes(), count=count, peak=peak,
                             unique=unique, fps=round(fps, 1), live=True)
                STATE["history"].append(count)


# ----------------------------- web UI -----------------------------
PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>trinetra · live</title>
<style>
:root{--bg:#0b0e13;--panel:#141a22;--line:#232c38;--txt:#e8edf3;--mut:#7d8b9c;--accent:#3ddc84}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.logo{font-size:22px;font-weight:700;letter-spacing:.3px}
.logo span{color:var(--accent)}
.live{display:flex;align-items:center;gap:7px;margin-left:auto;color:var(--mut);font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 0 rgba(61,220,132,.6);
animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(61,220,132,.5)}70%{box-shadow:0 0 0 9px rgba(61,220,132,0)}100%{box-shadow:0 0 0 0 rgba(61,220,132,0)}}
.grid{display:grid;grid-template-columns:1fr;gap:16px}
@media(min-width:820px){.grid{grid-template-columns:2fr 1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.videocard img{display:block;width:100%;background:#000;min-height:220px}
.src{padding:9px 14px;color:var(--mut);font-size:13px;border-top:1px solid var(--line);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}
.stat .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
.stat .v{font-size:38px;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums;line-height:1}
.stat.accent .v{color:var(--accent)}
.chartcard{grid-column:1/-1;padding:14px 16px}
.chartcard .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}
canvas{width:100%;height:90px;display:block}
footer{color:var(--mut);font-size:12px;margin-top:16px;text-align:center}
</style></head><body><div class=wrap>
<header><div class="logo">tri<span>netra</span></div>
<div class=live><span class=dot></span><span id=srclabel>connecting…</span></div></header>
<div class=grid>
  <div class="card videocard"><img src="/stream" alt="live feed">
    <div class=src id=src2>—</div></div>
  <div class=stats>
    <div class="stat accent"><div class=k>People now</div><div class=v id=count>–</div></div>
    <div class=stat><div class=k>Peak</div><div class=v id=peak>–</div></div>
    <div class=stat><div class=k>Unique seen</div><div class=v id=unique>–</div></div>
    <div class=stat><div class=k>NPU fps</div><div class=v id=fps>–</div></div>
  </div>
  <div class="card chartcard"><div class=k>Occupancy — last ~60s</div>
    <canvas id=spark width=1000 height=90></canvas></div>
</div>
<footer>trinetra · Hailo-8L · yolov6n on-chip · pet project</footer>
</div>
<script>
const $=id=>document.getElementById(id);
function draw(h){const c=$('spark'),x=c.getContext('2d'),W=c.width,H=c.height;
x.clearRect(0,0,W,H);if(!h.length)return;const mx=Math.max(2,...h),n=h.length,dx=W/Math.max(1,n-1);
x.beginPath();x.moveTo(0,H);for(let i=0;i<n;i++){x.lineTo(i*dx,H-(h[i]/mx)*(H-8)-2)}x.lineTo(W,H);x.closePath();
x.fillStyle='rgba(61,220,132,.15)';x.fill();
x.beginPath();for(let i=0;i<n;i++){const y=H-(h[i]/mx)*(H-8)-2;i?x.lineTo(i*dx,y):x.moveTo(i*dx,y)}
x.strokeStyle='#3ddc84';x.lineWidth=2;x.stroke();}
async function tick(){try{const j=await(await fetch('/stats')).json();
$('count').innerText=j.count;$('peak').innerText=j.peak;$('unique').innerText=j.unique;$('fps').innerText=j.fps;
$('srclabel').innerText=j.live?'live · '+j.source:'waiting for stream';$('src2').innerText=j.source;
draw(j.history);}catch(e){$('srclabel').innerText='disconnected'}}
setInterval(tick,500);tick();
</script></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/":
            self._send(200, "text/html", PAGE)
        elif self.path == "/stats":
            with LOCK:
                body = json.dumps({
                    "count": STATE["count"], "peak": STATE["peak"],
                    "unique": STATE["unique"], "fps": STATE["fps"],
                    "source": STATE["source"], "live": STATE["live"],
                    "history": list(STATE["history"]),
                }).encode()
            self._send(200, "application/json", body)
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    with LOCK:
                        jpg = STATE["jpeg"]
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                    time.sleep(0.08)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    threading.Thread(target=detect_loop, name="detect", daemon=True).start()
    print(f"[web] open http://0.0.0.0:{PORT}/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
