# FAILURE_TEST_REPORT.md

Runnable-now failure modes (component level, all green this session):
package-missing refusal (deployment gate), stale/duplicate/out-of-order
ticks (chaos + pipeline), unknown symbol, disconnect/reconnect,
terminal-unavailable, REAL-account execution refusal (live), order
reject/timeout paths (OMS/EMS suites), duplicate execution protection,
reconciliation mismatch handling, restart during session (GUI
lifecycle + recovery suites), db/disk-full/audit-unavailable fail-closed
(chaos). DEMO-session-specific failures (invalid DEMO account, DEMO
order reject/timeout/partial at the broker) require the DEMO login →
BLOCKED with §3. Targeted run 2026-09-26: 112 passed / 0 failed / 1
documented skip; validator PASS; build 9/9.
