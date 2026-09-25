# SECURITY.md — 1144 Trading OS

## LIVE safety

- **LIVE is structurally refused on every execution path** (fail-closed
  design; verified across the 2155-test suite incl. dedicated LIVE-refusal
  tests). No environment flag in this repository enables LIVE.
- Environment ladder: RESEARCH · SIMULATION · REPLAY · PAPER · BACKTEST ·
  DEMO · LIVE(refused). DEMO runtime is additionally gated on the MT5
  terminal being logged into a DEMO account (current blocker B1: terminal
  is on a REAL login, so every execution path correctly refuses).

## Secrets

- Repo rule (`.gitignore`, commented): *secrets must never be committed* —
  excludes `.env`, `*.secret`, `*.key`, `credentials*`, `*.local.yaml`.
- No credentials are stored in source; runtime state (`runtime/` SQLite
  stores, diagnostic bundles) is never committed.
- MT5/broker credentials live with the terminal/adapter environment, not in
  git. Audit logs and journals are scrubbed of credential material (security
  contract enums name credential KINDS, never values).

## Boundary rules (architecture-enforced)

- GF-002: the GUI never depends on database/event bus/adapters directly —
  it goes through the API layer only.
- GF-001: no module outside the ui domain depends on `ui.*`.
- All module dependencies are validated by the architecture validator
  (234+ rules; run `python -m architecture.validator`).

## Known security limitation (documented blocker B2)

The standalone Security/Quality audit tool
(`tools/security_quality_audit.py`) was deleted by an external modification
and could not be recovered; it must be re-supplied by the owner. In-suite
security validation (Phase 8 set, validator SEC rules) is green. See
`SECURITY_AUDIT_REPORT.md` and `docs/INTEGRATION_BLOCKERS.md`.

## Gateway network posture

- The gateway server binds locally (desktop workstation deployment model);
  no external network exposure by design. Transport authentication
  hardening is scheduled with the WebSocket transport phase (ecosystem
  Phases M/N).
