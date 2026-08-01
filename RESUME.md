# RasaOps — SAVE / RESUME FILE

**Read this first in any new session.**  
**Last saved:** 2026-08-01  
**Machine:** Windows PC (`C:\Users\tvikr\restaurant-ops-ai`)  
**Product:** RasaOps — Raspberry Pi 5 edge restaurant ops AI (India); host PC webcam used for lab  
**Active demo stream:** Coopers Live — https://www.youtube.com/watch?v=0JGQo-vAgwQ (`pub_bar`)

---

## Paste this into a new Grok / agent session

```
Continue RasaOps from RESUME.md at C:\Users\tvikr\restaurant-ops-ai\RESUME.md

1. Read docs/BEST_PRACTICES.md and follow it for UI/metrics/vision/privacy.
2. cd C:\Users\tvikr\restaurant-ops-ai ; activate .venv ; pip install -e ".[dev,edge]" ; pytest -q
3. Do not re-open architecture debates unless blocked.
4. Pick next work from "Next priorities" below.
5. Only one process may use the webcam at a time.
6. Cloud port default is 18080 (8080 is blocked/forbidden on this Windows host).
7. Prefer .venv\Scripts\python.exe only — avoid launching system Python twice.
8. Demo default YouTube: https://www.youtube.com/watch?v=0JGQo-vAgwQ (pub_bar).
```

## Canonical references

| Doc | Use |
|-----|-----|
| **`docs/BEST_PRACTICES.md`** | **Always** — UI, metrics, vision, zones, privacy, DevEx |
| `AGENTS.md` | Agent auto-rules (points at best practices) |
| `docs/architecture/RasaOps-System-Architecture.md` | System design |
| `README.md` | Quick start |

---

## One-line product

Edge-first restaurant ops: **local YOLO occupancy** on Pi 5 (or PC webcam) → **fail-closed privacy scrub** → **SQLite offline queue** → **HTTPS sync** → **cloud meta/insights/chat** → **local kiosk**. No facial recognition. Frames = personal data.

---

## Quick start after reboot

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,edge]"
pytest -q

# Clean one-shot: agent + live preview + browser (DEFAULT = Coopers pub)
.\scripts\Start-RasaOps-Demo.ps1

# Option A — webcam OpenCV only
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0

# Option B — full stack webcam (NO live video window)
python -m rasaops_edge.scripts.run_edge_agent --camera 0 --with-cloud
# Kiosk:  http://127.0.0.1:8090/

# Option C — Coopers Live pub (current default demo)
python -m rasaops_edge.scripts.run_edge_agent --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" --with-cloud --scene pub_bar --fps 0.5
python -m rasaops_edge.scripts.run_live_view --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" --scene pub_bar --fps 6 --max-height 480 --high-priority

# Option D — kitchen cooking + plating metrics
python -m rasaops_edge.scripts.run_edge_agent --youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" --with-cloud --scene kitchen_line --fps 0.5
```

**Never run two webcam apps together** — MSMF grab errors.  
YouTube mode does not need the webcam.  
**Never launch both `.venv` and system `Python312` agents** — doubles CPU/stream load.

---

## What is DONE (L1 lab vertical slice)

| ID | Item | State |
|----|------|--------|
| Arch | System architecture + Word export | Done |
| PR-01…03 | Monorepo, schemas, compose file, file capture | Done |
| PR-04 | ONNX YOLO + tiny model + factory (auto/hog/onnx/mock) | Done |
| PR-05 | ByteTrack-lite + occupancy + zones.v1 | Done |
| PR-06 | Fail-closed privacy scrubber + goldens | Done |
| PR-07 | SQLite event queue (retry / deadletter / frame TTL) | Done |
| PR-08 | Local FastAPI dashboard `/local/metrics` `/local/events` | Done |
| PR-09 | Kiosk UI EN/HI (static HTML, not React) | Done |
| PR-11…13 | Cloud API in-memory tenants + ingest + frame entitlement | Done |
| PR-14 | Edge sync agent HTTPS drain | Done |
| PR-16/17 | Rules-only insights + grounded chat | Done |
| Host cam | WebcamSource + YOLO11n export + desk zones | Done |
| YouTube | YoutubeSource + scene packs (pub / kitchen / dining) | Done |
| Pub metrics | bar pressure, social energy, drinks proxies | Done |
| Kitchen metrics | cooking_load, plating_pressure, line_pace, balance | Done |
| Live preview | low-latency drain, HIGH prio, multi-thread ORT | Done |
| Demo script | `scripts/Start-RasaOps-Demo.ps1` | Done |
| Tests | scene + kitchen metrics covered | Green |
| Docs | README, architecture `.md` + `.docx` | Done |

### Key paths

```
C:\Users\tvikr\restaurant-ops-ai\
  RESUME.md                          ← this file
  README.md
  docs\architecture\
    RasaOps-System-Architecture.md   ← source of truth
    RasaOps-System-Architecture.docx ← readable Word (open in Word)
  edge\rasaops_edge\
    capture\webcam_source.py         ← DSHOW preferred; auto-reopen on grab fail
    inference\                       ← onnx_yolo, hog, mock, factory
    tracking\ events\ privacy\
    queue\sqlite_queue.py            ← PR-07
    sync\agent.py                    ← PR-14
    dashboard_api\                   ← PR-08 + static\index.html kiosk
    agent.py                         ← full loop
    scripts\
      run_webcam_dogfood.py          ← LIVE VIDEO window (webcam)
      run_live_view.py               ← LIVE VIDEO + metrics overlay (YouTube/webcam)
      run_edge_agent.py              ← full stack (metrics kiosk)
    metrics\
      scene_metrics.py               ← dining / pub / kitchen packs
  edge\models\
    yolo11n_int8_320.onnx            ← real YOLO11n export (~10MB)
    tiny_yolo_like_320.onnx          ← CI only, not for live people
  cloud\api\
    app.py store.py                  ← L1 cloud (in-memory)
  shared\python\rasaops_shared\      ← EventMeta, zones
  shared\schemas\examples\
    zones.v1.desk_webcam.json        ← desk cam
    zones.v1.pub_live.json           ← Coopers / pub_bar
    zones.v1.kitchen_live.json       ← cook + plating stations
    zones.v1.restaurant_live.json    ← dining floor
    zones.v1.sample.json             ← sample dining
  scripts\Start-RasaOps-Demo.ps1     ← clean agent+preview+browser
  data\edge\                         ← SQLite queue + frame blobs (runtime)
  devops\docker-compose.yml          ← optional infra; NOT auto-started
```

---

## What is NOT done (do not claim pilot-ready)

| Item | Notes |
|------|--------|
| Docker stack running | Compose file exists; **containers never left running**. Docker Desktop empty unless user runs `compose up`. |
| Live video in browser kiosk | Only OpenCV window (`run_webcam_dogfood`). Kiosk is metrics/events only. |
| Real face detector for scrub | MockFaceDetector + head-ROI path; need YuNet/ONNX for pilot scrub |
| PR-15 MQTT device commands | Not implemented |
| Postgres + Alembic | Cloud is in-memory only |
| React + Playwright | Deferred |
| Pi image / CSI / thermal / systemd | L2 |
| mTLS, OTA, DSAR, counsel | L3 |
| Production Razorpay / multi-site | Post-pilot |

### Readiness levels (architecture)

- **L1** = lab dogfood (current) — **not** customer pilot  
- **L2** = Pi hardware exit  
- **L3** = external pilot after compliance  

---

## Ports (this Windows host)

| Port | Service |
|------|---------|
| **8090** | Edge kiosk API + UI |
| **18080** | Cloud API (default; use this) |
| **8080** | **Avoid** — bind forbidden / conflicted on this PC |
| 5432, 6379, 9000, 4222, 1883 | Docker compose infra if started |

---

## Known issues & fixes

1. **MSMF grab error** (`can't grab frame. Error: -1072875772`)  
   - Cause: camera busy (two Python apps) or MSMF backend flaky  
   - Fix: stop other agents; use one command; DSHOW preferred in `webcam_source.py`  
   - `$env:RASAOPS_CAMERA_BACKEND = "dshow"`

2. **HOG finds 0 people on desk cam**  
   - Normal — seated close-up. Use YOLO weights (`yolo11n_int8_320.onnx`); factory `auto` prefers them.

3. **Camera 1 black**  
   - Often NVIDIA virtual cam; use **camera 0**.

4. **Docker Desktop empty**  
   - Expected: we ran Python on host. Optional:  
     `docker compose -f devops\docker-compose.yml up -d`

5. **Long-running agent wastes CPU**  
   - Stop when not testing: kill `run_edge_agent` / `run_live_view` processes.  
   - When running agent + live preview: agent @ 0.5 FPS / 4 ORT threads; preview @ 6 FPS / 16 threads.

6. **Live preview lag on YouTube**  
   - Use `--max-height 480 --fps 6 --high-priority`, low-latency buffer drain (default in `run_live_view`).  
   - Prefer progressive MP4 formats from yt-dlp; CAP_FFMPEG only (no CAP_IMAGES fallback).

7. **Duplicate python.exe (venv + system)**  
   - Always start with `.\.venv\Scripts\python.exe` or `Start-RasaOps-Demo.ps1`.

---

## How to stop leftover processes

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'rasaops_edge|run_edge_agent|run_live_view|run_webcam' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## Next priorities (recommended order)

1. **Local MJPEG preview on kiosk** (optional UX) — if user wants live feed in browser  
2. **Real face redaction model** (YuNet/ONNX) wired into PR-06  
3. **PR-15** MQTT fanout + commands  
4. **Postgres** replace in-memory cloud store  
5. **Pi packaging** (PR-28) + hardware exit (PR-31)  
6. L3 compliance only when pilot is real  

---

## Product decisions locked

1. Pilot GTM: hardware deposit + discounted SaaS  
2. Frames: fail-closed when scrub OK; meta always  
3. MVP vision: occupancy / entry / queue only — not vision `task_completed`  
4. No facial recognition  
5. Cloud default port on this host: **18080**  
6. Lab demo default YouTube: **Coopers Live** `0JGQo-vAgwQ` → `pub_bar`

---

## Session log (brief)

| When | What |
|------|------|
| 2026-07-30 | Architecture design loop; monorepo; PR-01…05 |
| 2026-07-31 | PR-06 scrubber; RESUME/bootstrap |
| 2026-07-31 | Webcam + YOLO11n export; live dogfood; Word architecture |
| 2026-07-31 | Full L1: queue, dashboard, cloud, sync, edge agent; 45 tests |
| 2026-07-31 | Camera contention fix (DSHOW); Docker clarification |
| 2026-08-01 | Kitchen cooking/plating metrics + zones.v1.kitchen_live |
| 2026-08-01 | Live preview perf (ORT threads, low-latency, HIGH prio) |
| 2026-08-01 | **Demo default = Coopers Live pub**; Start-RasaOps-Demo.ps1 |
| 2026-08-01 | Metric/zone/UI fixes; kiosk redesign |
| 2026-08-01 | **docs/BEST_PRACTICES.md + AGENTS.md** (use every session) |

---

## Verify after resume

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
.\.venv\Scripts\Activate.ps1
pytest -q
# 45 passed (or more)

Test-Path edge\models\yolo11n_int8_320.onnx
# True

# Optional one-shot cam test (releases camera when done)
python edge\scripts\try_webcam_once.py 0
```

---

*End of save file. Update this document when a PR lands or ports/commands change.*
