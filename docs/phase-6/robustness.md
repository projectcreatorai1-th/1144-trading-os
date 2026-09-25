# Robustness (Phase 6)

- **WHAT**: Sensitivity classification: STABLE / SENSITIVE / UNSTABLE / UNKNOWN from stressed metric variation.
- **WHY**: Robustness is evidence, never a guarantee (SECTION 69).
- **BOUNDARY**: Sign-flip => UNSTABLE; magnitude test => SENSITIVE/STABLE.
- **SOURCE OF TRUTH**: core/research/pipeline.py:robustness_sensitivity
- **ASSUMPTIONS**: Based on net PnL variation across scenarios.
- **FAILURE**: No stressed runs => UNKNOWN.
- **RECOVERY**: n/a.
- **REPRODUCIBILITY**: Deterministic comparison.
- **TEST**: TestValidation::test_robustness_classification
