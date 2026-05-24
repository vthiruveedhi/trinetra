# trinetra

Video AI for venue analytics. From `त्रिनेत्र` — Sanskrit for "third eye": the eye of perception that sees what the other two miss.

Phase 1 = single MP4 in → annotated MP4 + per-person event log (entry, exit, dwell, zone presence, line crossings) in SQLite.

## The phased plan

1. **Phase 1** — single MP4 → annotated MP4 + SQLite events. *(implemented)*
2. **Phase 2** — live RTSP via Frigate → same event pipeline + Postgres.
3. **Phase 3** — multi-camera + re-identification (OSNet) + pgvector for "knows the regulars".
4. **Phase 4** — VLM Q&A layer (Qwen3-VL on server, MiniCPM-V on edge) over events + sampled frames.
5. **Phase 5** — dashboard + alerts.

## Stack

| Layer | Choice | License | Notes |
|---|---|---|---|
| Detection | YOLOv11n via `ultralytics` | AGPL-3.0 | POC only — swap to RT-DETR / YOLOX (Apache) before commercializing |
| Tracking | `supervision` (ByteTrack) | MIT | Also handles zones, lines, annotations |
| Re-ID (Phase 3+) | OSNet weights from Torchreid | MIT | Use weights directly; skip BoxMOT (AGPL) |
| Events DB | SQLite (Phase 1) → Postgres + pgvector (Phase 3+) | — | |
| VLM Q&A (Phase 4+) | Qwen3-VL (server), MiniCPM-V (edge) | Apache-2.0 | Not in hot path; called against event log + sampled keyframes |
| Live ingest (Phase 2+) | Frigate | MIT | Skipped in Phase 1 since it's built for live RTSP, not batch MP4 |

## Infra

- **Phase 1–2**: AWS m6i.large on Spot pricing, gp3 50 GB EBS for models. Treat the instance as a workstation — start when working, stop when not. ~$10/mo all-in.
- **Phase 3+**: scale to g4dn.xlarge (T4) or g5.xlarge (A10G). Same EBS detaches → attaches to the bigger box.
- All instances pinned to one AZ (EBS is AZ-bound; cross-AZ requires snapshot/restore).

## Privacy posture

- Face images **never stored**. Only embeddings, only where the feature requires them.
- Re-ID is session-scoped in Phase 1. Week+ "regulars" recognition (Phase 3+) crosses into biometric tracking — needs opt-in / signage / privacy policy under CCPA, BIPA, EU AI Act before going live.
- Demographics (age band) deferred — InsightFace weights are research-only; revisit with a permissively-trained model when the feature actually matters.

## Quick start

See [scripts/phase1/README.md](scripts/phase1/README.md).
