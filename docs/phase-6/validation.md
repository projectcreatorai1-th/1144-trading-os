# Validation (Phase 6)

- **WHAT**: Out-of-sample (frozen parameters) and cross-dataset validation.
- **WHY**: Unseen-data performance with frozen parameters is the minimum honesty check (SECTION 60-63).
- **BOUNDARY**: OOS never tunes parameters; logic hash pinned.
- **SOURCE OF TRUTH**: core/research/pipeline.py:out_of_sample
- **ASSUMPTIONS**: Train/test split by dataset content.
- **FAILURE**: INVALID results -> FAILED validation.
- **RECOVERY**: n/a.
- **REPRODUCIBILITY**: Frozen logic hash recorded.
- **TEST**: TestValidation::test_oos_frozen_parameters
