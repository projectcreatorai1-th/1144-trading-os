# State Rebuild (Phase 2)

- **WHAT**: Deterministic reconstruction of any entity's state from event history, optionally seeded by a verified snapshot (`core.state.rebuilder`).
- **WHY**: Current state is derived; it must always be reproducible from history, and divergence must be detectable (SECTIONS 9, 11, 32).
- **SOURCE OF TRUTH**: the event store. The rebuilder replays through the SAME projector rules used live - there is exactly one set of projection rules in the system.
- **INPUT**: entity (category + id), event range (from snapshot sequence / up to sequence), verified snapshot (optional).
- **OUTPUT**: `RebuildReport` (reconstructed state, applied event count).
- **IMMUTABILITY**: rebuild is pure - it writes nothing. Stored state is never touched by a rebuild.
- **FAILURE**: corrupted snapshots (hash mismatch) are rejected; snapshots of the wrong entity are rejected.
- **RECOVERY**: rebuild is the verification half of recovery: after restart or partial writes, current vs rebuilt must agree (consistency checker), otherwise INCONSISTENT (never assumed fine).
- **VERSION**: state contracts 1.0.0.
- **TEST**: `tests/test_phase2_projector_rebuild_recovery.py` (same events -> same state, snapshot seeding), `test_phase2_invariants_e2e.py` (restart->load->rebuild->verify).

## Determinism guarantees

- no clock reads: all state timestamps derive from the source event
- no randomness in projection decisions
- no external mutable state: handlers are pure functions of the event
- machine-backed categories validate through the Phase 0 state machine registry

Same events + same contract versions + same rules = same state (invariant-tested).
