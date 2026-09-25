# RECOVERY_VALIDATION_REPORT.md — 2026-09-25 (Phase 12 evidence)

Backup: hashed manifests (SHA-256 per file), retention pruning,
tamper detection — a flipped byte fails verification and the restore
REFUSES (fail closed). Restore tested for real (temp target + hash
re-verify). MEASURED RPO/RTO recorded per run (durations from perf
counters; no declared-without-measurement numbers).
Recovery: restore → reopen storage → read-back audit chain →
acknowledge; verification failure = no recovery (INV-REC-001).
Runbooks: 13 scenarios incl. the production disk-full (WinError 112)
class and MT5 disconnect/restart flows.
Disaster drills implemented as chaos scenarios (db unavailable, disk
full, audit unavailable, crash/relaunch via GUI lifecycle tests);
PC-crash/power-loss physical drills = TBD (single workstation; backup
path proven, physical power-cut not simulated).
DEMO-runtime recovery-in-anger = blocked by the REAL-account blocker.
