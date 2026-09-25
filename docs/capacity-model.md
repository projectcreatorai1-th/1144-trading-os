# Capacity Model (Phase 4)

- **WHAT**: CapacityAssessment (THEORETICAL/OBSERVED/ESTIMATED/UNKNOWN, provenance-required) + LiquidityBudget.
- **WHY**: Estimates are never observed facts; UNKNOWN is never a green light (SECTION 25/26).
- **SOURCE OF TRUTH**: core.portfolio.capacity.
- **INPUT**: Declared/observed capacity values + provenance.
- **OUTPUT**: is_exceeded_by -> True/False/None (None = UNKNOWN, callers fail closed).
- **IMMUTABILITY**: Frozen assessments.
- **FAILURE**: UNKNOWN-with-value is a contradiction; missing provenance rejected.
- **RECOVERY**: Assessments refreshed from sources.
- **VERSION**: schemas capacity/liquidity_budget 1.0.0.
- **TEST**: TestCapacityLiquidity
