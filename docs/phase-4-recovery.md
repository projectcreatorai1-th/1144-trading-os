# Phase 4 Recovery (Phase 4)

- **WHAT**: Recovery via idempotency + replay + convergence (no cross-store atomicity claims).
- **WHY**: Crashes between strategy/portfolio/risk writes must converge without duplicates (SECTION 37/50).
- **SOURCE OF TRUTH**: Append-only stores with unique ids (intent/decision/allocation).
- **INPUT**: Stored records.
- **OUTPUT**: Identical re-evaluations after restart.
- **IMMUTABILITY**: Duplicate canonical ids rejected everywhere.
- **FAILURE**: Partial writes leave the earlier truth intact; duplicate appends fail closed.
- **RECOVERY**: Restart -> reload -> re-evaluate -> identical result (E2E-10).
- **VERSION**: storage 2.3.0.
- **TEST**: invariants 22/23, E2E-10
