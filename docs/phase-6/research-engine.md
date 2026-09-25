# Research Engine (Phase 6)

- **WHAT**: ResearchPipeline: one deterministic run = backtest + bias audit + versioned result.
- **WHY**: Reproducible evidence for governance (SECTION 4-6).
- **BOUNDARY**: Research never mutates policy/portfolio/production state.
- **SOURCE OF TRUTH**: core/research/pipeline.py
- **ASSUMPTIONS**: Run identity = content hashes of dataset/strategy/config/model/policy/code/seed.
- **FAILURE**: Dependency hash mismatch fails closed; bias invalidates result.
- **RECOVERY**: Re-run converges (same identity).
- **REPRODUCIBILITY**: Same inputs -> same run_hash/result evidence hash (invariant-tested).
- **TEST**: TestBacktest::test_deterministic_identical_results
