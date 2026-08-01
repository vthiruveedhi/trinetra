# RasaOps

Edge-first restaurant ops AI for India: **Raspberry Pi 5** (or host PC webcam for lab) runs local occupancy vision; high-priority **event packages** sync to a multi-tenant cloud for insights and grounded chat.

**Task-only policy** — no facial recognition; frames treated as personal data with fail-closed redaction.

## L1 lab stack (what works today)

```
Webcam/CSI → YOLO → track → occupancy → privacy scrub → SQLite queue
       → sync HTTPS → Cloud ingest → rules insights + chat
       → local kiosk UI (EN/HI)
```

| Piece | Path |
|-------|------|
| Edge pipeline | `edge/rasaops_edge/` |
| Offline queue | `edge/rasaops_edge/queue/` |
| Sync agent | `edge/rasaops_edge/sync/` |
| Kiosk API + UI | `edge/rasaops_edge/dashboard_api/` |
| Cloud API | `cloud/api/` |
| Architecture (Word) | `docs/architecture/RasaOps-System-Architecture.docx` |

## Quick start (Windows)

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,edge]"

# Unit tests
pytest -q

# One-shot demo (kills old procs → agent + live preview + browser)
# Default stream: Coopers Live pub  https://www.youtube.com/watch?v=0JGQo-vAgwQ
.\scripts\Start-RasaOps-Demo.ps1

# Or full solution on host webcam:
python -m rasaops_edge.scripts.run_edge_agent --camera 0 --with-cloud
```

Then open:

- **Kiosk:** http://127.0.0.1:8090/
- **Cloud health:** http://127.0.0.1:18080/health
- **Cloud events:** http://127.0.0.1:18080/v1/events
- **Scene metrics:** http://127.0.0.1:8090/local/scene

### YouTube scene packs

| Stream | Scene | Zones | Metrics |
|--------|-------|-------|---------|
| [Coopers Live](https://www.youtube.com/watch?v=0JGQo-vAgwQ) (default) | `pub_bar` | `zones.v1.pub_live.json` | bar pressure, social energy, drinks |
| Kitchen / grill / servery | `kitchen_line` | `zones.v1.kitchen_live.json` | cooking load, plating pressure, line pace |
| Other restaurant lives | `dining_restaurant` | `zones.v1.restaurant_live.json` | floor load, tables, vibe |

```powershell
# Pub (default)
.\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" -Scene pub_bar

# Kitchen cooking + plating
.\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" -Scene kitchen_line
```

### Other entry points

```powershell
# Live OpenCV window only (high resources: 6 FPS, low-latency, 16 ORT threads)
python -m rasaops_edge.scripts.run_live_view --youtube --scene pub_bar --fps 6 --max-height 480

# Webcam preview only
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0

# Cloud only
python -m cloud.api

# Synthetic pipeline smoke (no camera)
python -m rasaops_edge.scripts.run_pipeline_smoke --scrub
```

## Privacy (non-negotiable)

- No facial recognition / no identity embeddings
- Fail-closed scrub before any cloud frame bytes
- Meta always uploads; frames only when scrub + entitlement allow
- Lite plan: `frames.upload=false` → meta only

## Readiness

| Level | Meaning |
|-------|---------|
| **L1** | Lab dogfood (this repo path) — **not** customer pilot |
| **L2** | Hardware exit on real Pi 5 |
| **L3** | External pilot after counsel + compliance PRs |

See:

- **`docs/BEST_PRACTICES.md`** — living best-practices reference (UI, metrics, vision, privacy); **use this for all product work**
- `AGENTS.md` — rules for coding agents
- `RESUME.md` — session status / how to resume
- `docs/architecture/` — full system design

## License

Proprietary — RasaOps Engineering.
