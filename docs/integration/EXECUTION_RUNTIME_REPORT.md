# EXECUTION_RUNTIME_REPORT.md — STATUS: BLOCKED (§3)

EMS/OMS + MT5 transport reused (no second execution system). Capability
gate EXEC-003 unblocked correctly for real hedging accounts after the
margin_mode enum fix (verified live: margin_mode=2→HEDGING). No DEMO
order/broker-ack/fill/position evidence can be produced while the
terminal holds a REAL login — every execution path fails closed by
design (re-proven 2026-09-25). Order lifecycle states incl.
REQUESTED/ACK/PARTIAL/FILLED/REJECTED/CANCELLED/UNKNOWN are covered by
the OMS/EMS suites (green) at component level.
