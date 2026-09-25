# Phase 3 — Risk Engine

- **WHAT**: Canonical risk context, dimension policies, hard limits, composition, the RiskEngine and RiskDecision 1.2.0 (`core.risk`).
- **WHY**: The system must know "how much risk is permitted right now, and why" - deterministically and auditably (SECTION 0/52).
- **SOURCE OF TRUTH**: inputs = state store + injected context; rules = policies; precedence/TTL/critical fields = `architecture/risk-config.yaml`.
- **INPUT**: `RiskEvaluationRequest` (action, subject, environment, requested exposure, context, correlation).
- **OUTPUT**: `RiskDecision` (permission, reasons, triggered/blocked rules, context+policy hashes, constraints, expiry, provenance fields).
- **IMMUTABILITY**: decisions + their contexts are append-only; the engine never mutates state, ledger or reconciliation.
- **FAILURE**: missing policy, critical-unknown context fields, hard-limit violations, environment mismatch, expired decisions - all BLOCK (fail-closed matrix).
- **RECOVERY**: deterministic evaluation - restart + re-evaluate yields the identical permission and hashes; replay verifies stored decisions.
- **VERSION**: risk_decision 1.2.0 (+action identity, rule evidence, hashes, constraints, provenance); risk_context/risk_budget 1.0.0.
- **TEST**: `tests/test_phase3_risk_engine.py`, `test_phase3_state_replay_recovery.py`, `test_phase3_invariants_e2e.py`.

## Engine order (RISK-004)

1. HARD SAFETY POLICIES first (GLOBAL_SAFETY_POLICY) - their verdict can never be softened (no AI/strategy/user override; risk-config forbids it).
2. Environment gate (decision carries request environment; validation enforces match).
3. Dimension policies in a fixed order (ACCOUNT, POSITION, EXPOSURE, DRAWDOWN, MARGIN, VOLATILITY, SPREAD, LIQUIDITY, CORRELATION, NEWS, EXECUTION_PERMISSION).
4. Composition: critical unknowns gate first (BLOCK), then most-severe permission wins (risk-config precedence), LIMITED constraints intersect (strictest).
5. Injected risk state gates (EMERGENCY -> EMERGENCY; PAUSE -> BLOCK for new risk).
6. Action-direction semantics: CLOSE/REDUCE stay permitted under CLOSE_ONLY (LIMITED).
7. No dimension AND no hard policy resolved -> BLOCK (missing policy fails closed).

## Dimensions

Each risk dimension is a POLICY TYPE carrying its rules - there is no separate per-dimension code path (no duplicated comparison logic): Capital/Position/Exposure/Drawdown/Margin/Volatility/Spread/Liquidity evaluate numeric limits; Correlation is UNKNOWN without data (critical field -> BLOCK, never guessed); News/Event risk enters as a context classification (NORMAL/ELEVATED/HIGH/EXTREME/UNKNOWN) and acts only as a constraint - never a trade direction.
