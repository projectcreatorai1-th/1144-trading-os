# Liquidity Budget (Phase 4)

- **WHAT**: LiquidityBudget: available/required/utilization/remaining + liquidity state.
- **WHY**: Liquidity constraints gate portfolio decisions; missing data is UNKNOWN (SECTION 26).
- **SOURCE OF TRUTH**: core.portfolio.capacity.LiquidityBudget.
- **INPUT**: Observed liquidity data.
- **OUTPUT**: covers(required) -> True/False/None.
- **IMMUTABILITY**: Frozen snapshot.
- **FAILURE**: UNKNOWN carries no numbers - no guessing.
- **RECOVERY**: Refreshed from observation.
- **VERSION**: 1.0.0.
- **TEST**: TestCapacityLiquidity
