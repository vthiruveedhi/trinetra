# Pilot-ready checklist (L3)

**Do not** treat L1 dogfood (PR-01…18) as pilot-ready.

## Required

- [ ] L2 Hardware Exit Criteria passed
- [ ] PR-19 privacy compliance productization (notices, DSAR, audit)
- [ ] PR-20 retention worker + legal hold path
- [ ] PR-21 model registry / offline eval evidence
- [ ] PR-25 cert lifecycle + remote wipe e2e
- [ ] Zones: PR-23 wizard **or** validated `zones.v1.json` import
- [ ] Counsel sign-off on DPDP notices and Fiduciary/Processor packaging
- [ ] Model quality gates: occupy/free precision/recall on labeled pilot hours
- [ ] Zero unredacted frames in pre-pilot cloud audit sample
- [ ] Support runbook + on-call for India business hours

## Exit metrics (pilot phase)

| Metric | Target |
|--------|--------|
| False alerts | &lt; 5 / site / day |
| Support tickets | ≤ 3 / site / week |
| Privacy Sev-1 | **0** |
