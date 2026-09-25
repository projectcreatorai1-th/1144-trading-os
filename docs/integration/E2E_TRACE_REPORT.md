# E2E_TRACE_REPORT.md

Identifiers exist and are pinned: correlation_id/trace_id/decision_id/
risk_decision_id/order_id/execution_id/position_id/reconciliation_id/
audit_id (identifiers registry 1.11.0). TraceAssembler stitches audit/
events per correlation_id with explicit GAP REPORTING (Phase 14, 6/6
tests) — Tick→…→Audit back-search is exercisable at component level.
A DEMO-session end-to-end trace is BLOCKED with §3 (no DEMO orders to
trace). STATUS: component PASS, runtime trace BLOCKED.
