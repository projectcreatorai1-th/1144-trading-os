# GATEWAY_HEALTH_REPORT.md

Status: IMPLEMENTED + TESTED (gateway health model §23, no composite
"strategy score")

- Session health: HEALTHY / DEGRADED / STALE / DISCONNECTED from
  last_seen/heartbeat_age/timeout (§15) — sweep transitions stale
  sessions; DEGRADED recovers on heartbeat.
- Server diagnostics (§24): uptime, contract/protocol versions,
  order_flow_open, active kills, sessions+metadata, queue depth,
  dropped/throttled, counters (messages/events/commands routed,
  rejected, duplicates, out_of_order, timeouts, reconnects, errors,
  kills). One-click: `python tools/gateway_diagnostics.py` (self-probe
  handshake+heartbeat, prints view, exports a SECRET-REDACTED bundle to
  runtime/gateway_diagnostic_bundle.json).
- Dependency health beyond the gateway (MT5/broker) belongs to the OS
  health plane (Phase 11 HealthAggregator) — not duplicated here.
