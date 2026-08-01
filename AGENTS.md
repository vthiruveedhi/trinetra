# RasaOps — Agent / contributor rules

This file is loaded automatically by Grok (and similar coding agents) in this repo.

## Always read first

1. `RESUME.md` — current lab status, ports, how to run  
2. **`docs/BEST_PRACTICES.md`** — **canonical best practices (UI, metrics, vision, privacy, DevEx)**  
3. `docs/architecture/RasaOps-System-Architecture.md` — system design (when architecture matters)

## Non-negotiables

- Follow `docs/BEST_PRACTICES.md` for every change to kiosk UI, scene metrics, zones, inference, privacy, or demo scripts.
- No facial recognition / identity embeddings. Frames = personal data; scrub fail-closed.
- Cloud port on this Windows host: **18080** (not 8080). Kiosk: **8090**.
- Prefer `.\.venv\Scripts\python.exe` only — do not launch system Python + venv duplicates.
- Label metric proxies honestly (drinks est, happiness proxy, etc.).
- When practices change, update `docs/BEST_PRACTICES.md` in the same change set.

## Default demo

- Stream: Coopers Live `https://www.youtube.com/watch?v=0JGQo-vAgwQ`
- Scene: `pub_bar`
- Script: `.\scripts\Start-RasaOps-Demo.ps1`

## Quick commands

```powershell
cd C:\Users\tvikr\restaurant-ops-ai
.\.venv\Scripts\Activate.ps1
pytest -q
.\scripts\Start-RasaOps-Demo.ps1
```

## Scope discipline

- Do not re-open architecture debates unless blocked.
- Do not claim L2/L3 or pilot-ready without checklist evidence.
- Prefer small, tested changes over large untested refactors.
