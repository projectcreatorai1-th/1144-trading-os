# Phase 2 — State

- **WHAT**: State engine, current-state store, transition history, snapshots, projector and processor (`core.state`).
- **WHY**: The system must answer deterministically "what do we believe the state is, and which event produced it" (SECTIONS 5-9).
- **SOURCE OF TRUTH**: Event store = history; state = derived, rebuildable representation. State is NEVER the truth - events are. Machine-backed categories (SYSTEM/MARKET/ORDER/POSITION) transition through the Phase 0 state machine registry; no parallel transition logic exists (SECTION 7).
- **INPUT**: accepted events (from the Phase 1 event store) + projection rules.
- **OUTPUT**: `StateRecord` (state_id, entity_type, entity_id, state_version, status, payload, effective/observed/processed times, source_event_id, correlation/causation, environment, schema_version) + immutable `StateTransitionRecord` (previous/new, event, reason, actor, versions).
- **IMMUTABILITY**: historical states and transitions are append-only (unique per entity+version; duplicate event application rejected). Frozen dataclasses; validator rule STATE-002.
- **FAILURE**: invalid machine transitions, version jumps, environment mismatches and missing event references fail closed with structured errors.
- **RECOVERY**: per-entity event idempotency (`has_applied_event`) makes replay safe; see docs/phase-2-recovery.md.
- **VERSION**: state_record/state_transition/state_snapshot schemas 1.0.0; identifiers registry 1.2.0.
- **TEST**: `tests/test_phase2_state_contracts.py`, `test_phase2_projector_rebuild_recovery.py`, `test_phase2_invariants_e2e.py`.

## Categories

SYSTEM_STATE, MARKET_STATE, DATA_STATE, ACCOUNT_STATE, ORDER_STATE, POSITION_STATE, LEDGER_STATE, RECONCILIATION_STATE. ORDER/POSITION are infrastructure only in Phase 2 - no execution behavior, no MT5.

## Processor

`EventStateLedgerProcessor.process(event)`: project state (if a rule applies) -> persist -> audit STATE_TRANSITION -> post ledger entries (registered handlers) -> emit `LEDGER_POSTED` event (only for NEW posts) -> audit LEDGER_POSTED. Events unrelated to state/ledger are passed through untouched.
