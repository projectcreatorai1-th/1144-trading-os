# TIME_GOVERNANCE_REPORT.md

Seven timestamp roles are separated across contracts (event/ingestion/
processing/decision/order/broker/execution). Server-local epoch is
handled canonically: per-tick measured offset (snap to half-hour,
±14h bound, residual>300s or future-drift>2s → fail closed),
ClockQuality records carry measured offset + confidence (today's live
re-measure: exactly +10800s = 3h EET, matching the canonical fix).
Regression tests: tests/test_phase10_finding_fixes.py (15/15 incl.
real-terminal case). STATUS: PASS (canonical, no machine-specific
workarounds).
