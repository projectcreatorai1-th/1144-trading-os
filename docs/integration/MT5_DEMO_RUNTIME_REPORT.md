# MT5_DEMO_RUNTIME_REPORT.md — STATUS: RUNTIME GATE BLOCKED (§3)

Blocked at prerequisites D/E (live probe 2026-09-26, evidence/
phase-1/PREREQUISITE_PROBE_2026-09-26.json): the terminal is logged
into a REAL account (login 411173797, trade_mode=2). AutoTrading is
also disabled (trade_allowed=False). §15 TEST-01..14 were NOT run —
no DEMO session exists. What IS real-terminal-verified (2026-09-25
gate rerun, real ticks on the same terminal): connection state machine,
tick ingestion through the Phase 1 pipeline with the canonical epoch
fix (accepted + freshness CURRENT), bounded long-run, disconnect/
reconnect, pause safety, REAL-account execution refusal. Those are
market-data/connectivity facts, NOT DEMO-labeled runtime evidence.
