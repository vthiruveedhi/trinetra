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

## Quick start

### macOS (Apple Silicon / Intel)

```bash
# clone / enter repo
cd restaurant-ops-ai   # monorepo root

bash scripts/mac/setup_mac.sh
bash scripts/mac/start_demo.sh          # Coopers pub YouTube + kiosk + preview
# bash scripts/mac/start_demo.sh --camera 0
# bash scripts/mac/stop_demo.sh
```

Full guide: [`scripts/mac/README.md`](scripts/mac/README.md)

### Windows

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,edge]"

pytest -q
.\scripts\Start-RasaOps-Demo.ps1

# Webcam:
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

```bash
# macOS
bash scripts/mac/start_demo.sh --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" --scene pub_bar
bash scripts/mac/start_demo.sh --youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" --scene kitchen_line
```

```powershell
# Windows
.\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" -Scene pub_bar
.\scripts\Start-RasaOps-Demo.ps1 -Youtube "https://www.youtube.com/watch?v=WSCyQJH_5TU" -Scene kitchen_line
```

### Other entry points

```bash
source .venv/bin/activate   # Mac/Linux
# .\.venv\Scripts\Activate.ps1  # Windows

python -m rasaops_edge.scripts.run_live_view --youtube --scene pub_bar --fps 3 --max-height 480
python -m rasaops_edge.scripts.run_webcam_dogfood --camera 0
python -m cloud.api
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
