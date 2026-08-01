# Hardware Exit Criteria (L2 lab exit)

**Gate:** required before Pilot OTA / leaving the lab (see architecture L2).  
**Does not replace L3** counsel/privacy/compliance gates for customer pilot.

## Checklist

- [ ] CSI camera opens on Pi 5; primary stream configured
- [ ] Sustained **2 FPS** sampling; local metric update **p95 ≤ 500 ms** (1 stream)
- [ ] **24 h soak**: no OOM; thermal backoff engages under load; no thrash reboot loop
- [ ] **Power-loss recovery**: SQLite queue integrity; no corrupt event packages
- [ ] **Fail-closed scrubber**: inject face-detector fault → **meta-only** uploads; zero unredacted frames in cloud audit sample
- [ ] mTLS reconnect after network drop
- [ ] OTA rollback drill (app package)
- [ ] (Multi-cam SKU only) Hailo path: 3-stream soak, metric p95 ≤ 1 s

## Scripts (to land with PR-31)

- `edge/scripts/soak_24h.sh`
- `edge/scripts/bench_pipeline.py`
- `edge/scripts/inject_scrubber_fault.py`

## Sign-off

| Role | Name | Date |
|------|------|------|
| Edge eng | | |
| Privacy eng (internal) | | |
