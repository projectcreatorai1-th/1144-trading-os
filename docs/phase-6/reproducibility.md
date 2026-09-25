# Reproducibility (Phase 6)

- **WHAT**: Deterministic identity + replay match + dependency hash verification.
- **WHY**: Same inputs + same versions + same data = same result (SECTION 5-6/56).
- **BOUNDARY**: Randomness requires an explicit versioned seed in provenance.
- **SOURCE OF TRUTH**: core/research/contracts.py:research_run_hash (content-based)
- **ASSUMPTIONS**: Identity ids excluded from hashes (registry handles, not content).
- **FAILURE**: Replay difference => MISMATCH reported, never auto-corrected.
- **RECOVERY**: Re-run reproduces identical hashes.
- **REPRODUCIBILITY**: Invariance-tested end to end.
- **TEST**: determinism + replay tests
