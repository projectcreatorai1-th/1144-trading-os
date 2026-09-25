# Phase 4 Replay (Phase 4)

- **WHAT**: Strategy/portfolio replay: re-run evaluations with historical inputs and compare.
- **WHY**: Historical decisions must be reconstructible for forensic/audit use (SECTION 36).
- **SOURCE OF TRUTH**: Deterministic engines + immutable stored versions (strategy/portfolio/policy).
- **INPUT**: Historical state + versions + context.
- **OUTPUT**: MATCH / MISMATCH / UNKNOWN / ERROR comparisons (read-only; no auto-correct).
- **IMMUTABILITY**: Replay never writes canonical records.
- **FAILURE**: Divergence reported as MISMATCH.
- **RECOVERY**: Replay IS the verification mechanism.
- **VERSION**: 1.0.0.
- **TEST**: E2E-9, E2E-10, determinism invariants
