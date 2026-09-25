# Policy Evaluation (Phase 3)

- **WHAT**: The single deterministic rule interpreter (`core.policy.evaluation.PolicyEvaluator`).
- **WHY**: rules must mean exactly one thing everywhere; same inputs -> same result and hashes (SECTION 7/22).
- **SOURCE OF TRUTH**: the policy document (conditions + limits); precedence from risk-config.yaml via the kernel registry loader.
- **INPUT**: policy + flattened risk context + environment + injected timestamp + correlation.
- **OUTPUT**: `PolicyEvaluation` (result, triggered rules with evidence, failed rules, warnings, context_hash, policy_hash).
- **IMMUTABILITY**: evaluations are stored append-only with their hashes.
- **FAILURE**: unknown limit references, invalid operators, non-numeric comparisons under numeric ops, environment mismatch, critical unknown fields -> BLOCK.
- **RECOVERY**: re-evaluation is byte-deterministic (no clock/random/global state).
- **VERSION**: policy_evaluation schema 1.0.0; interpreter version tracked in risk-config `risk_rule_version`.
- **TEST**: evaluator DSL tests in `tests/test_phase3_policy.py`.

## Reproducibility

context_hash = sha256(canonical context content); policy_hash = sha256(canonical policy semantic content). Identical (policy version, context, environment) -> identical result AND identical hashes - verified by invariant test #20.
