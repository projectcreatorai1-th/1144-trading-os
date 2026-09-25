# Bias Audit (Phase 6)

- **WHAT**: BiasAuditor: LOOK_AHEAD, DATA_LEAKAGE, SURVIVORSHIP, SELECTION, REPAINTING, FUTURE_NORMALIZATION, TIMEZONE, SESSION checks.
- **WHY**: Biased backtests are worse than no backtests (SECTION 10-14/74-77).
- **BOUNDARY**: LOOK_AHEAD FAIL or critical UNKNOWN => run INVALID.
- **SOURCE OF TRUTH**: core/research/bias.py
- **ASSUMPTIONS**: Critical bias classes: look-ahead, leakage, future label/feature.
- **FAILURE**: Detected violations produce evidence strings, never silent clipping.
- **RECOVERY**: n/a (audit).
- **REPRODUCIBILITY**: Pure function of dataset + decisions.
- **TEST**: TestBias
