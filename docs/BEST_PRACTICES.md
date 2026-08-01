# RasaOps Best Practices (Reference)

**Status:** Living reference — use on every feature, UI change, metric, and vision tweak.  
**Last updated:** 2026-08-01  
**Audience:** Humans + AI agents working in this repo  

Sources distilled from industry dashboard UX research, operational monitoring patterns, edge computer-vision production practice, and privacy-by-design guidance (dashboard KPI design, real-time ops layouts, edge YOLO / tracking hygiene, workplace CCTV privacy). This document **adapts** those norms to RasaOps L1–L3 constraints.

---

## How to use this doc

1. **Before coding** — skim the section that matches the work (UI / metrics / vision / privacy / DevEx).
2. **While coding** — treat “must” items as non-negotiable; “should” as default unless you document a reason.
3. **In PRs / agent sessions** — call out any deliberate deviation in the change summary.
4. **When in doubt** — prefer honesty of metrics and privacy fail-closed over flashy numbers.

Canonical pointer for agents: root `AGENTS.md` → this file.

---

## 1. Product & metrics philosophy

### Must

| Rule | Why |
|------|-----|
| **Decisions first, metrics second** | Every number on the kiosk should answer “what should staff do?” (seat, clear bar, push plates). |
| **Label proxies honestly** | Happiness, drinks, vibe, cooking load are **proxies**, not ground truth. Never market as emotion AI or FR. |
| **Meta always; frames fail-closed** | Event meta can upload; frame bytes only after scrub + entitlement. |
| **No facial recognition / identity embeddings** | Task- and zone-only tracking; track IDs stay local. |
| **One primary audience per surface** | Kiosk = floor manager (ops, seconds). Cloud chat = questions. Don’t mix executive quarterly KPIs into the kiosk. |

### Should

- Prefer **right-time** cadence: kiosk ~1–2 s refresh; cloud insights can be slower.
- Cap primary KPI tiles to **4–9** at the top; everything else is secondary.
- Separate **detected** vs **estimated** values in the UI (`drinks YOLO` vs `drinks est`).

### Metric quality bar

| Metric | Prefer | Avoid |
|--------|--------|--------|
| People count | Max of stable tracks + person dets; filter tiny/huge boxes | Raw detection spam, double-count |
| Table / seat | Feet/seat association; exclusive zone assignment; capacity_hint | Centroid-only; overlapping multi-count |
| Drinks | YOLO drinkware + honest “est from seated” when model blind | Claiming “drinks sold” |
| Floor load | Thresholds from percentiles (p60/p85), scene-type floors | `busy = avg+1` (never triggers) |
| Bar crowd | People in bar/rail **queue** zones | Only scanning `kind=table` |

---

## 2. Kiosk / dashboard UX

Adapted from operational dashboard practice: glanceable status in 3–6 seconds, consistent status color, three-tier layout (health → department → drill-down).

### Layout (must)

```
┌─────────────────────────────────────────────────┐
│ Top bar: brand · stream · scene · online · clock │
├─────────────────────────────────────────────────┤
│ Hero KPIs (4): People · Seating · Load · Bar    │  ← 3–6s glance
├─────────────────────────────────────────────────┤
│ Context panel: Pub  OR  Kitchen  OR  Dining     │
├─────────────────────────────────────────────────┤
│ Room pulse / density / vibe (secondary)         │
├─────────────────────────────────────────────────┤
│ Floor zones (cards with fill bars)              │
├──────────────────────┬──────────────────────────┤
│ Live events          │ Ask / chat               │
└──────────────────────┴──────────────────────────┘
```

### Must

- **One job per view** — ops monitoring, not a data warehouse explorer.
- **Status color = meaning** — green/ok, amber/warn, red/hot; never rainbow decoration.
- **Largest number = most actionable** (people, seats, load).
- **Dark, high-contrast kiosk theme**; WCAG-ish contrast for text on panels.
- **Touch targets** ≥ ~44px for EN/HI, Ask, pills.
- **No nested card-in-card clutter**; fixed DOM sections show/hide by scene.
- **Hard-refresh friendly** — static HTML/CSS/JS under `dashboard_api/static/`.

### Should

- Hero: **exactly four** primary KPIs unless a scene truly needs a fifth.
- Seat fill as a **progress bar**, not only text.
- Per-zone cards: state + people/cap + fill bar.
- Events: newest first, compact time, zone label, scrub status.
- EN/HI strings via a single `T` map; don’t hardcode only English in new UI.

### Anti-patterns

- Dumping raw JSON on the floor UI.
- Injecting ad-hoc sections with `document.createElement` every refresh.
- Identical styling for “free” and “occupied”.
- Hiding that a number is estimated.

**UI source of truth:** `edge/rasaops_edge/dashboard_api/static/index.html`

---

## 3. Scene packs & zones

### Must

| Scene | Zones file | Metrics pack |
|-------|------------|--------------|
| `pub_bar` | `zones.v1.pub_live.json` | `scene.pub` |
| `kitchen_line` | `zones.v1.kitchen_live.json` | `scene.kitchen` |
| `dining_restaurant` | `zones.v1.restaurant_live.json` or desk | floor + vibe |
| Webcam desk | `zones.v1.desk_webcam.json` | dining defaults |

- Zones are **640×480 canvas** after letterbox (match pipeline).
- Zone `kind`: `table` | `entrance` | `queue` | `pass` | `other`.
- **Bar/rail** → `queue` with id/label containing `bar` / `rail` / `counter`.
- **Plating** → `pass`.
- **Cook stations** → `table` (or pass) with `cook` / `grill` in id for kitchen metrics.

### Association rules (must)

1. Use **feet** (bottom ~88% of bbox), not pure centroid, for seating.
2. **Exclusive** assignment: each person → at most one zone (smallest matching table first, then pass/queue).
3. Capacity via `capacity_hint`; report `seated_people`, `seat_capacity`, `seats_free_est`.

### Should

- Calibrate zones from a real snapshot when available (`data/edge/*_calib.jpg`).
- Keep polygons non-overlapping for tables where possible.
- Enter/leave times shorter for lab (~1.5s / ~6s) than pilot floor defaults.

---

## 4. Vision / inference / tracking

### Must

- **Host lab (Windows/macOS):** `auto` prefers **Ultralytics yolo11n.pt @ imgsz 640** when present — 320px ONNX often undercounts crowded pubs by 50%+ (e.g. 8 vs ~20 true).
- **Pi product path:** **YOLO11n INT8 ONNX 320** (`yolo11n_int8_320.onnx`); never use `tiny_yolo_like` for live people.
- Backend factory: `auto` → ultralytics (host) → ONNX → HOG → mock. Force with `RASAOPS_INFERENCE_BACKEND=onnx|ultralytics`.
- **Person conf** ~0.15 (ultralytics) / ~0.18–0.22 (onnx); **small objects** ~0.10–0.15.
- Always treat `person_count` as **model estimate**, not certified headcount — show note in UI.
- Filter person boxes: min area, max ~65% frame, reject absurd aspect ratios.
- Tracker: IoU match ~0.25; keep age budget higher when agent FPS is low.
- Atmosphere classes only when scene needs drinks/food proxies (pub/dining/kitchen).

### Should

- Live preview: low-latency drain, decode height ≤480, HIGH priority, more ORT threads than the agent.
- Agent: lower FPS (0.5–1) when preview is running so one process doesn’t starve the other.
- Prefer **one** `.venv` Python — never system + venv double agents.

### YouTube lab streams

- Prefer `player_client: android,ios` then fallbacks; optional cookies via env (never hard-crash if Chrome DB locked).
- Env: `RASAOPS_YT_COOKIES_FROM_BROWSER`, `RASAOPS_YT_COOKIES_FILE`, `RASAOPS_ORT_THREADS`.

### Anti-patterns

- Running webcam dogfood and edge agent on the same camera.
- Claiming 100% people recall on crowded pub CCTV at 320px.
- Shipping track_id to cloud.

---

## 5. Privacy & compliance posture

### Must

- **No FR, no biometric identity.**
- Scrub before any cloud frame bytes; fail closed.
- Redaction status visible on events in kiosk.
- Frames entitlement from plan/config (`frames.upload`).
- Document proxies in UI footers / disclaimers.

### Should

- Prefer meta-only paths for Lite plans.
- Keep DSAR / retention work on L3 checklist; don’t invent production compliance claims in L1.

**Related:** architecture doc privacy sections; `edge/rasaops_edge/privacy/`.

---

## 6. API & data contracts

### Must

| Endpoint | Role |
|----------|------|
| `GET /local/metrics` | Kiosk payload: tables, seating, scene, online, stream_title |
| `GET /local/scene` | Scene-only snapshot |
| `GET /local/events` | Recent edge events |
| Cloud `:18080` | Ingest / chat / health (**not** 8080 on this Windows host) |

- Kiosk port default **8090**.
- Scene object always includes: `person_count`, `floor_load`, `thresholds`, scene-specific `pub` / `kitchen` when applicable.
- Tables entries: `zone_id`, `label`, `state`, `person_count_est`, `capacity_hint`, `fill_rate`.

### Should

- Promote `seating`, `bar_crowd_est`, `person_count` to top-level metrics for UI simplicity.
- Keep `raw_metrics` for debug only — not primary UI binding.

---

## 7. Engineering / DevEx

### Must

```bash
# macOS preferred clean demo
bash scripts/mac/setup_mac.sh
bash scripts/mac/start_demo.sh
bash scripts/mac/stop_demo.sh
```

```powershell
# Windows preferred clean demo
.\scripts\Start-RasaOps-Demo.ps1

# Explicit (any platform, after venv activate)
python -m rasaops_edge.scripts.run_edge_agent --youtube URL --with-cloud --scene pub_bar
python -m rasaops_edge.scripts.run_live_view --youtube URL --scene pub_bar --fps 3 --max-height 480
```

- Tests: `pytest -q` before claiming green.
- After metric threshold experiments, reset `data/edge/scene_metrics_*.json` if retune went bad.
- Ports: **8090** kiosk, **18080** cloud.

### Should

- One process pair: one agent + optional one live view.
- Document new scene packs in README + this file’s zone table.
- Prefer small pure functions for geometry / metrics (unit-tested).

### Session bootstrap

Read `RESUME.md` first, then this file, then change code.

---

## 8. Checklist: changing the kiosk UI

- [ ] Hero KPIs still ≤ 4 primary tiles  
- [ ] Status colors only encode state  
- [ ] Estimated metrics labeled (`~`, “est”, or note)  
- [ ] Pub / kitchen panels hide when unused  
- [ ] EN/HI strings updated in `T`  
- [ ] Works at 1280×720 and ~tablet width  
- [ ] No dependency on external CDNs if avoidable (kiosk offline)  
- [ ] Hard-refresh verified against running agent  

---

## 9. Checklist: changing metrics / vision

- [ ] Proxy disclaimer still accurate  
- [ ] Feet + exclusive zones still used for seating  
- [ ] Person vs object conf thresholds still split  
- [ ] Scene pack wired in `run_edge_agent` / `run_live_view` auto-detect  
- [ ] Unit tests for new metric branches  
- [ ] Kiosk fields bound (or intentionally omitted)  
- [ ] Load thresholds won’t “never fire” after improve()  

---

## 10. Glossary

| Term | Meaning in RasaOps |
|------|-------------------|
| **Proxy** | Heuristic signal (motion, COCO cup, seating), not sales or emotion |
| **Scrub** | Fail-closed redaction before frame upload |
| **Scene pack** | Thresholds + extra metrics for pub / kitchen / dining |
| **Feet point** | Bottom-center of person box for zone membership |
| **L1 / L2 / L3** | Lab dogfood / Pi hardware exit / external pilot |

---

## 11. Revision log

| Date | Change |
|------|--------|
| 2026-08-01 | Initial best-practices reference from web ops/dashboard/CV/privacy norms + RasaOps lab learnings |

When you update practices, **edit this file in the same PR** and bump the revision log.
