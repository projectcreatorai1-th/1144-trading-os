# Research Artifacts (Phase 6)

- **WHAT**: Versioned research artifacts: datasets, configs, execution models, runs, results, bias/stress reports - all content-hashed.
- **WHY**: Immutable evidence chain (SECTION 79-80).
- **BOUNDARY**: Artifacts are never edited; new evidence creates new artifacts.
- **SOURCE OF TRUTH**: core/research/contracts.py hash discipline
- **ASSUMPTIONS**: Hashes are sha-256 over canonical JSON.
- **FAILURE**: Hash mismatch => corruption => fail closed.
- **RECOVERY**: Rebuild from source stores.
- **REPRODUCIBILITY**: Hash verification on every validate().
- **TEST**: hash tests across all contract tests
