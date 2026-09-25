# Policy Lifecycle (Phase 3)

- **WHAT**: DRAFT -> REVIEW -> APPROVED -> ACTIVE -> SUSPENDED -> RETIRED (machine registry 1.1.0 + registry service).
- **WHY**: policies with safety impact must not self-activate; every semantic change must be visible (SECTION 5/39).
- **SOURCE OF TRUTH**: `architecture/state-machines.yaml#policy_status` (machine) + `core.policy.registry` (service-level strictness).
- **INPUT**: policy documents + actor contexts (role, user) + injected timestamps.
- **OUTPUT**: new immutable policy versions + audit records for every privileged action.
- **IMMUTABILITY**: same-version overwrites are rejected; lifecycle moves create patch-bumped versions; full history queryable (`iter_versions`, `version_at_time`).
- **FAILURE**: activation without APPROVED, self-approval, missing permission, ambiguous precedence - rejected (fail closed).
- **RECOVERY**: restart-safe store; deterministic resolution.
- **VERSION**: state-machines registry 1.1.0 (REVIEW/APPROVED added; DRAFT->ACTIVE edge retained for emergency operator use, never used programmatically).
- **TEST**: lifecycle + permission + audit tests in `tests/test_phase3_policy.py`.

## Authority boundaries

| Action | Required permission | Constraint |
|---|---|---|
| create | MODIFY_POLICY | starts DRAFT |
| submit_review | MODIFY_POLICY | DRAFT only |
| approve | APPROVE | approver != author (POLICY-003) |
| activate | MODIFY_POLICY | APPROVED only (service level) |
| suspend | PAUSE | audited |
| retire | MODIFY_POLICY | audited |

Roles come from the Phase 0 permission registry; AI holds no role and no path to any of these actions.
