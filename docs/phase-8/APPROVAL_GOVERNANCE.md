# Approval + Governance (Maker-Checker)

Flow (state machine approval_flow): DRAFT -> SUBMITTED -> REVIEW
[HUMAN_APPROVAL] -> APPROVED -> ACTIVE, with REJECTED evidence kept.

- The ApprovalRecord contract makes self-approval UNREPRESENTABLE
  (checker == maker is a validation error).
- Non-human checkers (AI_MODEL, SYSTEM_AUTOMATED) can never approve
  (SEC-006/037/038).
- Stale approvals (TTL exceeded) never grant.
- Approval records are immutable; rejection preserves evidence.

GovernanceGate wraps the EXISTING lifecycles (Phase 3/4/7 registries are
untouched): every governed transition proves (1) audit chain integrity,
(2) an authenticated maker holding the artifact permission, (3) an
ACTIVE approval by a different human made for THIS operation and maker
(approval spoofing blocked), (4) a versioned artifact with old/new
content hashes - then records an immutable GovernanceTransition
(WHO/WHAT/WHEN/WHY/OLD/NEW/APPROVAL/EVIDENCE) and appends chained audit.

Artifact permission routing: POLICY->MODIFY_POLICY, RISK_CONFIG->
MODIFY_RISK, STRATEGY->MODIFY_POLICY, MODEL->APPROVE, SECURITY_CONFIG->
MANAGE_SECURITY, ROLE_PERMISSION_CONFIG->MANAGE_GOVERNANCE,
ENVIRONMENT_CONFIG->CONFIGURE.
