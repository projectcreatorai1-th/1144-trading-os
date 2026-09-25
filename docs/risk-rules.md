# Risk Rules (Phase 3)

- **WHAT**: Rule representation, dimension mapping, hard limits and the risk budget.
- **WHY**: one interpreter, one comparison semantics, one arithmetic source (SECTIONS 9/10/18/19).
- **SOURCE OF TRUTH**: rule content lives in policies; numbers live in policy.limits; precedence/TTL/critical fields in risk-config.yaml; money semantics in currencies.yaml (Phase 2).
- **INPUT/OUTPUT**: see the policy evaluation doc.
- **IMMUTABILITY/FAILURE/RECOVERY**: as per policy evaluation (rules are policy content).
- **VERSION**: risk_rule_version 1.0.0 in risk-config.yaml (bump on any interpreter semantic change).
- **TEST**: dimension tests in `tests/test_phase3_risk_engine.py`, budget tests in `test_phase3_invariants_e2e.py`.

## Dimension -> policy type mapping

Capital->ACCOUNT_RISK_POLICY, Position->POSITION_RISK_POLICY, Exposure->EXPOSURE_POLICY, Drawdown->DRAWDOWN_POLICY, Margin->MARGIN_POLICY, Volatility->VOLATILITY_POLICY, Spread->SPREAD_POLICY, Liquidity->LIQUIDITY_POLICY, Correlation->CORRELATION_POLICY, News/Event->NEWS_RISK_POLICY, Execution permission->EXECUTION_PERMISSION_POLICY, Global hard safety->GLOBAL_SAFETY_POLICY.

## Hard limits

Hard limits are GLOBAL_SAFETY_POLICY rules evaluated FIRST; their BLOCK/CLOSE_ONLY/EMERGENCY cannot be softened by any dimension result, AI input, strategy or user policy (risk-config `hard_limit_override_allowed: false`, enforced structurally - the engine has no override parameter at all).

## Risk budget (centralized arithmetic)

RiskBudget (schema risk_budget 1.0.0): allocated/used/requested as decimal strings; `remaining = allocated - used`, `projected = used + requested` - computed ONLY in `core.risk.budget`; scopes ACCOUNT/STRATEGY/SYMBOL/DIRECTION/PORTFOLIO (constraints only - no optimizer).
