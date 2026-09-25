# Stress Testing (Phase 6)

- **WHAT**: StressTest over versioned scenarios (spread/slippage/commission multipliers).
- **WHY**: Robustness under degraded execution (SECTION 65-66).
- **BOUNDARY**: Scenario parameters live in configuration, never hard-coded.
- **SOURCE OF TRUTH**: core/research/pipeline.py:stress_test
- **ASSUMPTIONS**: Scaled configs re-hashed and re-validated.
- **FAILURE**: Unknown costs in any scenario -> fail closed.
- **RECOVERY**: n/a.
- **REPRODUCIBILITY**: Scenario set versioned via config_version.
- **TEST**: TestValidation::test_stress_scenarios_versioned
