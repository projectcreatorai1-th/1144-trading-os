# Phase 2 — Recovery & Crash Safety

- **WHAT**: Explicit consistency/recovery mechanisms over state, ledger, snapshot and event stores.
- **WHY**: Storage transactions do not span stores; the system must converge deterministically after crashes without duplicate state or ledger (SECTIONS 33-35).
- **SOURCE OF TRUTH**: the event store. Everything derived (state, ledger view) can be rebuilt from it.
- **INPUT**: stored events (all of them).
- **OUTPUT**: converged state/ledger + consistency verdicts (CONSISTENT / INCONSISTENT / UNKNOWN).

## Transactional boundary (documented limitation - SECTION 35)

Event append, state save and ledger post are SEPARATE SQLite transactions. No cross-store atomicity is claimed. Instead: every operation is idempotent (state: per-entity event idempotency; ledger: deterministic idempotency keys; LEDGER_POSTED events: emitted only for newly created entries), so reprocessing the full event history converges to exactly-once canonical effects.

## Crash scenarios tested (SECTION 34)

- crash BEFORE state write: event exists; recovery applies state + ledger once.
- crash AFTER state write, BEFORE ledger write: recovery skips the applied state, posts the missing ledger entry once.
- crash AFTER ledger write: recovery emits no duplicate LEDGER_POSTED; balances stay single-counted.
- restart during ingestion: reopening the database yields identical state/balances (restart->load->rebuild->verify E2E).
- duplicate recovery: running `recover()` twice changes nothing.

## Verification

- `StateConsistencyChecker`: current vs rebuilt per entity.
- `LedgerConsistencyChecker`: store balance vs LEDGER_POSTED event reconstruction.
- Both return UNKNOWN when evidence is missing - UNKNOWN is never treated as consistent.
