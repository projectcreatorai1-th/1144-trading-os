# Risk State Machine (Phase 3)

- **WHAT**: The risk_state machine (Phase 0 registry) driven through the Phase 2 state machinery as RISK_STATE category.
- **WHY**: risk state changes must be legal, immutable, event-linked and auditable - never ad-hoc strings (SECTION 11).
- **SOURCE OF TRUTH**: `architecture/state-machines.yaml#risk_state`; transitions recorded via `core.state` (RISK_STATE category added in state contracts 1.1.0).
- **INPUT**: engine decision permission (escalation) or operator action (recovery) + triggering event.
- **OUTPUT**: StateRecord + StateTransitionRecord + audit RISK_STATE_TRANSITION (causation-linked to the event).
- **IMMUTABILITY**: state history append-only (Phase 2 store guarantees).
- **FAILURE**: illegal transitions (e.g. EMERGENCY -> NORMAL directly) fail closed via the machine.
- **RECOVERY**: stepwise only (EMERGENCY -> PAUSE -> LIMITED -> CAUTION -> NORMAL), operator-driven; escalation may jump per the machine.
- **VERSION**: state contracts 1.1.0; state-machines registry unchanged for risk_state (Phase 0 definition reused as-is).
- **TEST**: `tests/test_phase3_state_replay_recovery.py`.

## Escalation mapping (deterministic)

| Decision permission | Risk-state target |
|---|---|
| EMERGENCY | EMERGENCY |
| CLOSE_ONLY | LIMITED |
| BLOCK | CAUTION |
| ALLOW / LIMITED | (no escalation) |

The engine reads the CURRENT risk state via explicit injection (`set_risk_state`) - no hidden mutable globals.
