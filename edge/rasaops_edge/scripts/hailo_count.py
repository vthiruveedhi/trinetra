#!/usr/bin/env python3
"""
trinetra — live restaurant analytics on the Hailo-8L NPU.

RUN UNDER SYSTEM python3 (numpy 1.x + cv2 + hailo_platform), NOT the venv:

    MODEL=/usr/share/hailo-models/yolov8s_h8l.hef \
    SOURCE_URL='https://www.youtube.com/watch?v=8V-N6xSL_X8' \
      python3 edge/rasaops_edge/scripts/hailo_count.py

Open http://<pi-ip>:8090.

Metrics:
  • People now / Peak / Avg-60s  — occupancy (tracker).
  • Entered / Left               — door line-crossing counter (footfall).
  • Unique (re-ID)               — distinct people via an in-memory appearance
                                   gallery so someone who moves or is briefly
                                   occluded is NOT recounted. Appearance-based
                                   (HSV histogram) → approximate; OSNet-on-Hailo
                                   + pgvector is the production upgrade (Phase 3).

Tune the door line for your camera:
  DOOR_LINE="x1,y1,x2,y2"   (stream pixels; default is a guess, drawn on video)
  DOOR_FLIP=1               (swap entered/left if the default is backwards)
"""
import collections
import json
import os
import shutil
import statistics
import subprocess
import threading
import time

import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hailo_platform import (HEF, VDevice, ConfigureParams, HailoStreamInterface,
                            InferVStreams, InputVStreamParams, OutputVStreamParams,
                            FormatType)

MODEL = os.environ.get("MODEL", "/usr/share/hailo-models/yolov6n_h8l.hef")
SOURCE_URL = (os.environ.get("SOURCE_URL") or os.environ.get("RTSP_URL")
              or "rtsp://cO1ErY4y:XzMU6lh3g5wIUVxs@192.168.68.55:554/live/ch0")
PORT = int(os.environ.get("PORT", "8090"))
PERSON_CLASS = 0
SCORE_TH = float(os.environ.get("SCORE_TH", "0.35"))
YT_MAX_H = int(os.environ.get("YT_MAX_HEIGHT", "480"))
YT_REFRESH_SEC = 300.0
REID_TH = float(os.environ.get("REID_TH", "0.55"))      # cosine sim to call it same person
GALLERY_TTL = float(os.environ.get("GALLERY_TTL", "25"))  # sec a lost person is remembered
DOOR_FLIP = os.environ.get("DOOR_FLIP", "0") not in ("0", "", "false", "no")

STATE = {"jpeg": None, "now": 0, "peak": 0, "avg60": 0.0, "fps": 0.0,
         "entered": 0, "left": 0, "unique": 0,
         "model": os.path.basename(MODEL), "source": "", "live": False,
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
    binp = _yt_dlp_bin()
    if not binp:
        raise RuntimeError("yt-dlp not found (venv + PATH). Set YT_DLP_BIN.")
    fmt = (f"best[height<={YT_MAX_H}][ext=mp4][protocol^=http]/"
           f"best[height<={YT_MAX_H}][ext=mp4]/best[height<={YT_MAX_H}]/best[ext=mp4]/best")
    # --force-ipv4: googlevideo CDN over a half-broken IPv6 route is the usual
    # cause of "Connection timed out" when fetching segments on a Pi.
    base = ["-g", "-f", fmt, "--no-warnings", "--quiet", "--no-playlist", "--force-ipv4"]
    last = None
    for ea in ("youtube:player_client=android,ios",
               "youtube:player_client=tv_embedded,mweb", None):
        cmd = [binp, *base] + (["--extractor-args", ea] if ea else []) + [page_url]
        try:
            out = subprocess.check_output(cmd, text=True, timeout=60,
                                          stderr=subprocess.STDOUT).strip().splitlines()
            if out and out[0].startswith("http"):
                return out[0]
        except Exception as e:
            last = e
    raise RuntimeError(f"yt-dlp could not resolve {page_url}: {last}")


class Source:
    def __init__(self, url):
        self.url = url
        self.is_yt = ("youtube.com" in url) or ("youtu.be" in url)
        self.cap = None
        self.opened_at = 0.0
        self.label = "YouTube live" if self.is_yt else "RTSP " + url.split("@")[-1]

    def open(self):
        self.close()
        if self.is_yt:
            target = resolve_youtube(self.url)
            # fail a stuck segment fetch in ~8s instead of hanging ~30s
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rw_timeout;8000000"
        else:
            target = self.url
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
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
            try:
                self.open()
            except Exception:
                return None
        if self.is_yt and time.time() - self.opened_at > YT_REFRESH_SEC:
            try:
                self.open()
            except Exception:
                pass
        if self.cap is None:          # open() failed above — don't touch None
            return None
        ok, frame = self.cap.read()
        return frame if (ok and frame is not None and frame.size) else None

    def close(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


# ----------------------------- re-ID + tracking -----------------------------
def embed(crop):
    """Cheap appearance fingerprint: normalized HSV hue-sat histogram."""
    if crop is None or crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 4:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().astype(np.float32)


def cosine(a, b):
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6))


def iou(a, b):
    ix1, iy1, ix2, iy2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    return inter / ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter)


def line_side(pt, l1, l2):
    return (l2[0]-l1[0])*(pt[1]-l1[1]) - (l2[1]-l1[1])*(pt[0]-l1[0])


class PeopleTracker:
    """IoU tracking + in-memory re-ID gallery + door line crossing."""
    def __init__(self, iou_th=0.25, max_missed=30, min_hits=3):
        self.iou_th, self.max_missed, self.min_hits = iou_th, max_missed, min_hits
        self.tracks = {}          # local_id -> dict
        self.gallery = {}         # global_id -> {"emb","last"}
        self.next_local = 1
        self.next_global = 1
        self.unique = 0
        self.entered = 0
        self.left = 0
        self.line = None          # ((x1,y1),(x2,y2))

    def _reid(self, emb, tnow):
        """Return a global_id: reuse a recently-lost match, else mint a new one."""
        best_id, best = None, REID_TH
        for gid, g in list(self.gallery.items()):
            if tnow - g["last"] > GALLERY_TTL:
                del self.gallery[gid]
                continue
            s = cosine(emb, g["emb"])
            if s > best:
                best, best_id = s, gid
        if best_id is not None:
            self.gallery.pop(best_id, None)
            return best_id
        gid = self.next_global
        self.next_global += 1
        self.unique += 1
        return gid

    def update(self, boxes, frame, tnow):
        for t in self.tracks.values():
            t["matched"] = False
        # match detections to existing local tracks by IoU
        for box in boxes:
            best_id, best = None, self.iou_th
            for tid, t in self.tracks.items():
                if t["matched"]:
                    continue
                i = iou(t["box"], box)
                if i > best:
                    best, best_id = i, tid
            cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
            if best_id is not None:
                t = self.tracks[best_id]
                t.update(box=box, cx=cx, cy=cy, missed=0, matched=True, hits=t["hits"] + 1)
            else:
                crop = frame[max(0, box[1]):box[3], max(0, box[0]):box[2]]
                gid = self._reid(embed(crop), tnow)
                self.tracks[self.next_local] = {
                    "box": box, "cx": cx, "cy": cy, "missed": 0, "matched": True,
                    "hits": 1, "gid": gid, "side": None, "emb": embed(crop)}
                self.next_local += 1

        # door line crossing (confirmed tracks only)
        if self.line is not None:
            l1, l2 = self.line
            for t in self.tracks.values():
                if t["missed"] != 0 or t["hits"] < self.min_hits:
                    continue
                s = line_side((t["cx"], t["cy"]), l1, l2)
                cur = 1 if s > 0 else (-1 if s < 0 else 0)
                if t["side"] and cur and cur != t["side"]:
                    inward = cur < 0
                    if DOOR_FLIP:
                        inward = not inward
                    if inward:
                        self.entered += 1
                    else:
                        self.left += 1
                if cur:
                    t["side"] = cur

        # age out; remember lost tracks in the gallery for re-ID
        for tid in list(self.tracks):
            t = self.tracks[tid]
            if not t["matched"]:
                t["missed"] += 1
                if t["missed"] > self.max_missed:
                    if t["emb"] is not None:
                        self.gallery[t["gid"]] = {"emb": t["emb"], "last": tnow}
                    del self.tracks[tid]

        return [(t["gid"], t["box"]) for t in self.tracks.values()
                if t["missed"] == 0 and t["hits"] >= self.min_hits]


# ----------------------------- detector -----------------------------
class HailoPersonDetector:
    def __init__(self):
        self.hef = HEF(MODEL)
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
                    boxes.append((int(xmin*w), int(ymin*h), int(xmax*w), int(ymax*h)))
        return boxes


def door_line_for(w, h):
    env = os.environ.get("DOOR_LINE", "").strip()
    if env:
        try:
            x1, y1, x2, y2 = (int(v) for v in env.split(","))
            return ((x1, y1), (x2, y2))
        except Exception:
            print(f"[door] bad DOOR_LINE={env!r}, using default")
    # default guess: vertical-ish line ~62% across (tune per camera)
    return ((int(0.62 * w), int(0.15 * h)), (int(0.66 * w), int(0.80 * h)))


def detect_loop():
    det = HailoPersonDetector()
    tracker = PeopleTracker()
    src = Source(SOURCE_URL)
    with LOCK:
        STATE["source"] = src.label
    print(f"[hailo] model {os.path.basename(MODEL)} loaded. source = {src.label}")
    peak, t_prev, fail, last_sample = 0, time.time(), 0, 0.0
    recent = collections.deque(maxlen=8)
    line_set = False
    while True:
        try:
            frame = src.read()
        except Exception as e:
            print(f"[source] {e}; retry 3s")
            time.sleep(3)
            continue
        if frame is None:
            fail += 1
            if fail % 20 == 0:
                try:
                    src.open()
                except Exception as e:
                    print(f"[source] reopen failed: {e}")
            time.sleep(0.05)
            continue
        fail = 0

        h, w = frame.shape[:2]
        if not line_set:
            tracker.line = door_line_for(w, h)
            line_set = True

        tnow = time.time()
        active = tracker.update(det.detect(frame), frame, tnow)
        recent.append(len(active))
        now_count = int(round(statistics.median(recent)))
        peak = max(peak, now_count)

        fps = 1.0 / max(1e-3, tnow - t_prev)
        t_prev = tnow
        if tnow - last_sample >= 1.0:
            last_sample = tnow
            with LOCK:
                STATE["history"].append(now_count)
                hist = list(STATE["history"])[-60:]
                STATE["avg60"] = round(sum(hist) / max(1, len(hist)), 1)

        # draw door line + labels
        (lx1, ly1), (lx2, ly2) = tracker.line
        cv2.line(frame, (lx1, ly1), (lx2, ly2), (255, 200, 0), 2)
        cv2.putText(frame, "DOOR", (lx1 - 10, ly1 - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (255, 200, 0), 2)
        for gid, (x1, y1, x2, y2) in active:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 220, 90), 3)
            cv2.putText(frame, f"#{gid}", (x1, max(12, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 220, 90), 2)
        cv2.rectangle(frame, (0, 0), (w, 34), (0, 0, 0), -1)
        cv2.putText(frame, f"occupancy {now_count}   entered {tracker.entered}   "
                           f"left {tracker.left}   unique {tracker.unique}",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (60, 220, 90), 2)

        ok_enc, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if ok_enc:
            with LOCK:
                STATE.update(jpeg=buf.tobytes(), now=now_count, peak=peak,
                             fps=round(fps, 1), entered=tracker.entered,
                             left=tracker.left, unique=tracker.unique, live=True)


# ----------------------------- web UI -----------------------------
PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>trinetra · live</title>
<style>
:root{--bg:#0b0e13;--panel:#141a22;--line:#232c38;--txt:#e8edf3;--mut:#7d8b9c;--accent:#3ddc84;--blue:#5b9dff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:18px}
header{display:flex;align-items:center;gap:12px;margin-bottom:16px}
.logo{font-size:22px;font-weight:700}.logo span{color:var(--accent)}
.live{display:flex;align-items:center;gap:7px;margin-left:auto;color:var(--mut);font-size:14px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--accent);animation:pulse 1.6s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(61,220,132,.5)}70%{box-shadow:0 0 0 9px rgba(61,220,132,0)}100%{box-shadow:0 0 0 0 rgba(61,220,132,0)}}
.grid{display:grid;grid-template-columns:1fr;gap:16px}
@media(min-width:860px){.grid{grid-template-columns:1.7fr 1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.videocard img{display:block;width:100%;background:#000;min-height:220px}
.src{padding:9px 14px;color:var(--mut);font-size:13px;border-top:1px solid var(--line);
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-content:start}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}
.stat .k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
.stat .v{font-size:34px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums;line-height:1.05}
.stat .s{color:var(--mut);font-size:11px;margin-top:5px}
.stat.accent .v{color:var(--accent)}.stat.blue .v{color:var(--blue)}
.stat.eng .v{font-size:18px;font-weight:600;margin-top:8px}
.chartcard{grid-column:1/-1;padding:14px 16px}
.chartcard .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px}
canvas{width:100%;height:90px;display:block}
footer{color:var(--mut);font-size:12px;margin-top:16px;text-align:center;line-height:1.6}
</style></head><body><div class=wrap>
<header><div class="logo">tri<span>netra</span></div>
<div class=live><span class=dot></span><span id=srclabel>connecting…</span></div></header>
<div class=grid>
  <div class="card videocard"><img src="/stream" alt="live feed">
    <div class=src id=src2>—</div></div>
  <div class=stats>
    <div class="stat accent"><div class=k>People now</div><div class=v id=now>–</div>
      <div class=s>in frame right now</div></div>
    <div class=stat><div class=k>Peak</div><div class=v id=peak>–</div>
      <div class=s>most at once</div></div>
    <div class="stat blue"><div class=k>Entered</div><div class=v id=entered>–</div>
      <div class=s>crossed the door in</div></div>
    <div class=stat><div class=k>Left</div><div class=v id=left>–</div>
      <div class=s>crossed the door out</div></div>
    <div class=stat><div class=k>Unique · re-ID</div><div class=v id=unique>–</div>
      <div class=s>distinct people (approx)</div></div>
    <div class=stat><div class=k>Avg · 60s</div><div class=v id=avg>–</div>
      <div class=s>rolling average</div></div>
    <div class="stat eng"><div class=k>Engine</div><div class=v id=eng>–</div>
      <div class=s>on-chip speed</div></div>
  </div>
  <div class="card chartcard"><div class=k>Occupancy — last 60s</div>
    <canvas id=spark width=1000 height=90></canvas></div>
</div>
<footer>trinetra · Hailo-8L NPU · <span id=modelf>—</span><br>
re-ID is appearance-based (approx); doorway "entered" is the reliable footfall number</footer>
</div>
<script>
const $=id=>document.getElementById(id);
function draw(h){const c=$('spark'),x=c.getContext('2d'),W=c.width,H=c.height;
x.clearRect(0,0,W,H);if(!h.length)return;const mx=Math.max(2,...h),n=h.length,dx=W/Math.max(1,n-1);
x.beginPath();x.moveTo(0,H);for(let i=0;i<n;i++)x.lineTo(i*dx,H-(h[i]/mx)*(H-8)-2);x.lineTo(W,H);x.closePath();
x.fillStyle='rgba(61,220,132,.15)';x.fill();
x.beginPath();for(let i=0;i<n;i++){const y=H-(h[i]/mx)*(H-8)-2;i?x.lineTo(i*dx,y):x.moveTo(i*dx,y)}
x.strokeStyle='#3ddc84';x.lineWidth=2;x.stroke();}
async function tick(){try{const j=await(await fetch('/stats')).json();
$('now').innerText=j.now;$('peak').innerText=j.peak;$('avg').innerText=j.avg60;
$('entered').innerText=j.entered;$('left').innerText=j.left;$('unique').innerText=j.unique;
$('eng').innerText=j.fps+' fps';$('modelf').innerText=j.model;
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
                    "now": STATE["now"], "peak": STATE["peak"], "avg60": STATE["avg60"],
                    "entered": STATE["entered"], "left": STATE["left"], "unique": STATE["unique"],
                    "fps": STATE["fps"], "model": STATE["model"], "source": STATE["source"],
                    "live": STATE["live"], "history": list(STATE["history"]),
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
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        os._exit(0)  # skip Hailo C++ destructors that abort noisily at shutdown


if __name__ == "__main__":
    main()
