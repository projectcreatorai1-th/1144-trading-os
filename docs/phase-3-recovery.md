# Phase 3 — Recovery

- **WHAT**: Restart/recovery behavior for the policy + risk subsystems.
- **WHY**: evaluations must be reproducible after crashes; nothing may double-authorize or drift (SECTION 30 recovery matrix).
- **SOURCE OF TRUTH**: the stores (policies, evaluations, decisions + contexts) - all SQLite, append-only, restart-safe.
- **INPUT**: stored requests/decisions.
- **OUTPUT**: identical re-evaluations; replay verification MATCH.
- **IMMUTABILITY**: all Phase 3 stores reject duplicate ids / same-version overwrites.
- **FAILURE**: partial writes leave the older truth intact; re-evaluation is idempotent in content (same permission/hashes; new decision id for a new evaluation is expected and harmless - decisions are evaluations, not mutations).
- **RECOVERY**: no cross-store atomicity is claimed (same as Phase 2): determinism + append-only + replay-verify replace distributed transactions.
- **VERSION**: storage schema 2.2.0.
- **TEST**: restart/reevaluate/verify + duplicate-evaluation tests in `tests/test_phase3_state_replay_recovery.py`.

## Tested scenarios

- crash before decision persistence: event exists -> re-evaluate -> identical result
- crash after decision persistence: reload -> replay -> MATCH
- restart: reopen store, rebuild registry/engine, re-evaluate -> identical permission + hashes
- duplicate evaluation: same request -> same reasons/triggered rules (content-deterministic)
- duplicate policy version write: rejected by the store
- replay twice: same comparison verdict
