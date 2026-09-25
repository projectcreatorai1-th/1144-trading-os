# Execution Model (Phase 6)

- **WHAT**: ExecutionModel contract: fill models, spread/slippage/commission/latency/liquidity/market-impact assumptions, intrabar policy - all versioned + hashed.
- **WHY**: Backtest results must be attributable to their execution assumptions (SECTION 33-42/89).
- **BOUNDARY**: No assumption of infinite liquidity, zero latency or free fills without declaration.
- **SOURCE OF TRUTH**: core/research/contracts.py:ExecutionModel
- **ASSUMPTIONS**: liquidity UNKNOWN / market-impact ASSUMPTION are explicit labels.
- **FAILURE**: Hash mismatch -> fail closed.
- **RECOVERY**: Version lock: model change invalidates old results.
- **REPRODUCIBILITY**: model_hash commits to all assumption content.
- **TEST**: TestBacktest::test_dependency_hash_mismatch_detected
