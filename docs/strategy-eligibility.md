# Strategy Eligibility (Phase 4)

- **WHAT**: EligibilityResult: ELIGIBLE / CONDITIONALLY_ELIGIBLE / INELIGIBLE / UNKNOWN.
- **WHY**: Strategies may only propose intents when lifecycle, environment, capability, config, data quality and market state allow (SECTION 13).
- **SOURCE OF TRUTH**: core.strategy.evaluation.StrategyEligibilityEvaluator.
- **INPUT**: Strategy + capability + config + risk context + symbol + time.
- **OUTPUT**: Verdict + machine-readable reasons (explainability from evidence).
- **IMMUTABILITY**: Frozen result dataclass.
- **FAILURE**: UNKNOWN critical inputs never become automatically eligible; INVALID/STALE data is INELIGIBLE.
- **RECOVERY**: Deterministic - re-evaluation reproduces the verdict.
- **VERSION**: schema strategy_eligibility 1.0.0.
- **TEST**: TestEvaluationAndEligibility
