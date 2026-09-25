# Execution State Machine (Phase 5)

- **WHAT**: order_state machine 1.3.0: Phase 0 intent chain (SIGNAL..CLOSED) + Phase 5 execution chain (CREATED->VALIDATING->ACCEPTED->ROUTING->SENT->ACKNOWLEDGED->PARTIALLY_FILLED->FILLED) + failure/cancel/replace/UNKNOWN paths.
- **WHY**: No skipped or mutated states; every transition append-only with event/reason/actor/version (SECTION 5).
- **BOUNDARY**: Machine transitions validated by the Phase 0 engine.
- **SOURCE OF TRUTH**: architecture/state-machines.yaml#order_state.
- **INPUT**: OMS lifecycle operations.
- **OUTPUT**: Immutable order versions per transition.
- **FAILURE BEHAVIOR**: Invalid transitions rejected; broker-driven outcomes reach only legal states.
- **RECOVERY**: UNKNOWN -> poll/reconcile -> legal confirmed states.
- **AUDIT**: Each transition audited.
- **TEST**: machine checks in test_phase5_boundary_oms.py
