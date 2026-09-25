# Phase 3 — Policy

- **WHAT**: Policy contract 1.1.0, registry with full lifecycle, deterministic resolution and the rule evaluator (`core.policy`).
- **WHY**: Policy is "the rules the system must obey" - separated from risk (the evaluation) and from execution (SECTION 4).
- **SOURCE OF TRUTH**: policy documents in the policy store (immutable versions); rule semantics in the policy itself.
- **INPUT**: `Policy` documents (declarative rules + limits), actor contexts (permissions), environment, effective time.
- **OUTPUT**: lifecycle transitions (audited), resolved active policies, `PolicyEvaluation` results (reproducible, hashed).
- **IMMUTABILITY**: versions are append-only; lifecycle moves write patch-bumped new versions - history is never overwritten.
- **FAILURE**: unresolvable/ambiguous resolution, missing approval, self-approval, environment mismatch, unknown limits -> all fail closed.
- **RECOVERY**: policy store is restart-safe; resolution is deterministic per (type, environment, time).
- **VERSION**: policy schema 1.1.0 (+REVIEW/APPROVED lifecycle, +12 risk policy types, +identity fields); policy_evaluation 1.0.0.
- **TEST**: `tests/test_phase3_policy.py` (lifecycle, approval separation, precedence, evaluator DSL).

## Lifecycle

DRAFT -> REVIEW -> APPROVED -> ACTIVE -> SUSPENDED/RETIRED. The registry activates ONLY APPROVED policies (the machine-level DRAFT->ACTIVE edge stays for emergency operator use). Self-approval is forbidden (author != approver, rule POLICY-003). Every privileged action requires a Phase 0 permission (MODIFY_POLICY / APPROVE / PAUSE) and is audited.

## Rule DSL (policy.conditions)

`{"rule_id", "dimension", "field" (dotted path into the risk context), "op" (<= >= < > == !=), "limit" (name into policy.limits), "on_trigger" (ALLOW/LIMITED/BLOCK/CLOSE_ONLY/EMERGENCY), "constraint" (LIMITED limits), "critical"}`

Rules express REQUIREMENTS and trigger on VIOLATION. Numeric fields compare as Decimals; enum fields compare as strings under ==/!= only. Unknown critical fields -> BLOCK; unknown limits/operators -> failed rule -> BLOCK.
