# Fail-Closed Matrix (Phase 3)

- **WHAT**: The hard fail-closed behavior map (SECTION 16) - implemented and tested.
- **WHY**: nothing may silently degrade to ALLOW when the picture is unclear.
- **SOURCE OF TRUTH**: risk-config.yaml (critical unknown fields); the matrix below is enforced in composition/engine/validator/registry.
- **INPUT/OUTPUT**: n/a (behavior map).
- **IMMUTABILITY**: matrix changes are contract changes (risk-config version bump).
- **FAILURE**: the matrix itself IS the failure specification.
- **RECOVERY**: every BLOCK is auditable and replayable; unblocking requires the underlying condition to change (policy/data/state), never a bypass.
- **VERSION**: risk-config 1.0.0.
- **TEST**: scattered across phase 3 tests - each row has at least one deterministic test.

| Condition | Result | Enforced by |
|---|---|---|
| missing policy (no dimension + no hard resolved) | BLOCK | engine |
| expired policy (outside effective window) | not resolved -> BLOCK | registry resolution |
| unknown critical context field | BLOCK (+ UNKNOWN_CRITICAL reasons) | composition |
| invalid data (INVALID quality) | contract-level blocking decision | risk_decision contract |
| stale/degraded critical data | blocks risk allowance (DEGRADED/STALE/INVALID quality) | risk_decision contract |
| expired risk decision | never validates | validator |
| environment mismatch | never validates / engine gate | validator + evaluator |
| policy hash mismatch | never validates | validator |
| risk context hash mismatch | never validates | validator |
| invalid state transition | fail closed (machine error) | Phase 0 machine |
| unknown permission | fail closed (no precedence entry) | risk config severity_rank |
| validator failure | invalid decision | validator |
| system emergency (risk state EMERGENCY) | EMERGENCY permission | engine gate |
| global pause (risk state PAUSE) | BLOCK for new risk | engine gate |
| ambiguity (same-priority active policies) | resolution error (fail closed) | registry |
