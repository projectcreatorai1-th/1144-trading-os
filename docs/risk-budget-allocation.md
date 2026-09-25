# Risk Budget Allocation (Phase 4)

- **WHAT**: Strategy risk budgets REUSE Phase 3 RiskBudget (allocated/used/requested -> remaining/projected).
- **WHY**: One budget contract system-wide (SECTION 21; no duplicate RiskBudget).
- **SOURCE OF TRUTH**: core.risk.budget (Phase 3) - reused, never duplicated.
- **INPUT**: Membership risk_budget + used/requested values.
- **OUTPUT**: RiskBudget instances with within-budget verdicts.
- **IMMUTABILITY**: Frozen Phase 3 contract.
- **FAILURE**: Over-budget requests flagged as RISK_CONFLICT (never auto-closed).
- **RECOVERY**: Deterministic recomputation.
- **VERSION**: risk_budget 1.0.0 (Phase 3).
- **TEST**: TestPortfolioDecision (risk conflict) + validator ALLOCATION-002
