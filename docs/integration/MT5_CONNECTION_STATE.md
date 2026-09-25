# MT5_CONNECTION_STATE.md

Machine: registered connection_state (architecture/state-machines.yaml)
with deterministic transitions; monitored by ConnectionMonitor with
connectivity.yaml backoff (500ms→8s ×2.0 cap 10). Proven: connect/
disconnect/reconnect live (2026-09-25 gate); stale detection via policy
thresholds incl. the monitor-less fail-open fix (chaos-found, fixed);
unknown-symbol and terminal-unavailable refusal covered by phase-10
suites (green). All transitions auditable + versioned. STATUS: PASS
(component-level; runtime DEMO run blocked at §3).
