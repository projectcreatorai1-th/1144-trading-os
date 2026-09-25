# Replay (Phase 6)

- **WHAT**: Replay comparison: recorded vs replayed metrics/equity at every level (SECTION 53-57).
- **WHY**: Determinism proof + forensic diff.
- **BOUNDARY**: Read-only; never sends orders or mutates state.
- **SOURCE OF TRUTH**: core/research/pipeline.py:replay_compare
- **ASSUMPTIONS**: Comparisons are exact string-canonical.
- **FAILURE**: Differences -> MISMATCH with per-metric/per-point detail.
- **RECOVERY**: n/a (read-only).
- **REPRODUCIBILITY**: Match proven by identical hashes.
- **TEST**: TestValidation::test_replay_compare_match_and_mismatch
