# Strategy Portfolio Risk Flow (Phase 4)

- **WHAT**: The SECTION 62 boundary: STATE -> POLICY -> RISK -> RISK DECISION -> STRATEGY -> INTENT -> PORTFOLIO -> PROJECTED EXPOSURE -> RISK AGAIN (same engine) -> RISK DECISION -> [Phase 5 OMS].
- **WHY**: Risk appears before AND after strategy/portfolio - but there is exactly ONE risk engine.
- **SOURCE OF TRUTH**: core.strategy.gate.IntentGate re-using core.risk.engine.RiskEngine.
- **INPUT**: Intent + portfolio decision + base risk context.
- **OUTPUT**: IntentPermission (final permission for the intent) + full audit chain.
- **IMMUTABILITY**: All records append-only.
- **FAILURE**: Environment mismatch fail-closed; expired intents rejected; strategies not in the decision rejected; projected exposure never understates known exposure (max of base/projected).
- **RECOVERY**: Deterministic re-authorization reproduces permissions and hashes.
- **VERSION**: 1.0.0.
- **TEST**: tests/test_phase4_gate_e2e.py (E2E 1-10 + invariants)
