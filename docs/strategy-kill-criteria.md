# Strategy Kill Criteria (Phase 4)

- **WHAT**: KillCriteriaEvaluation: ACTIVE / WARNING / SUSPEND_REQUIRED / KILL_REQUIRED from versioned criteria rules against supplied evidence.
- **WHY**: Strategies must be stoppable by deterministic, policy-controlled criteria without self-override (SECTION 14).
- **SOURCE OF TRUTH**: Criteria rules (versioned) + evidence supplied via contract (drift/execution-quality boundaries are inputs).
- **INPUT**: Evidence mapping (drawdown, failures, capacity, liquidity, spread...).
- **OUTPUT**: Worst-triggered verdict + evidence.
- **IMMUTABILITY**: Frozen result.
- **FAILURE**: Unknown evidence yields WARNING (never silent ACTIVE); rule violations map to severities.
- **RECOVERY**: Criteria re-evaluated deterministically on new evidence.
- **VERSION**: schema kill_criteria 1.0.0.
- **TEST**: TestKillAndHealth
