# Risk Context (Phase 3)

- **WHAT**: Canonical point-in-time evaluation input (`core.risk.context.RiskContext`, schema risk_context 1.0.0).
- **WHY**: risk decisions must be based on exactly what was known THEN - no future data, no guessed values (SECTIONS 8/20).
- **SOURCE OF TRUTH**: state store (account balances, data quality, system state) + explicit runtime inputs; the builder never invents values.
- **INPUT**: `as_of` timestamp, environment, optional overrides, entity hints (account/data-source/system entity).
- **OUTPUT**: frozen context with content hash; dotted-path flattened view for rule resolution.
- **IMMUTABILITY**: frozen dataclass; stored alongside every decision.
- **FAILURE**: binary-float values rejected; enum fields validated; UNKNOWN stays None and critical unknowns force BLOCK (risk-config list).
- **RECOVERY**: contexts rebuild deterministically from stored content (exact hash match).
- **VERSION**: risk_context 1.0.0.
- **TEST**: context tests inside `tests/test_phase3_risk_engine.py` + builder tests.

## Sections

- ACCOUNT: balance, equity, free/used margin, margin level, available capital
- POSITIONS: count, long/short/net/gross exposure, per-symbol, per-strategy
- RISK: current/projected risk pct, daily loss(+pct), drawdown(+pct), peak equity, recovery state
- MARKET: state, volatility, spread, liquidity classifications
- DATA: quality level, stale/missing/invalid/unknown flags
- SYSTEM: system state, execution state, environment
- EVENT: active events, event risk (NORMAL/ELEVATED/HIGH/EXTREME/UNKNOWN), window, severity

All monetary values are canonical decimal strings; None = UNKNOWN (never safe).
