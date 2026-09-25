# Authorization

AuthorizationService is the single decision boundary. It delegates the
permission question to the one canonical source -
platform.security.RolePermissionRegistry over permissions.yaml - and
adds the guardrails: session validity, session-environment binding,
environment permission restrictions, approval requirements for the
configured privileged operations, and strong-authentication for LIVE.

Decisions are deterministic (same inputs -> same decision + reasons)
and are returned as evidence (ALLOW/BLOCK + reasons); BLOCK is terminal
for security - it never upgrades.

APPROVAL_REQUIRED_OPERATIONS (SECTION 13): LIVE_TRADE_AUTHORIZATION,
SECURITY_POLICY_CHANGE, ROLE_PERMISSION_CHANGE, CREDENTIAL_AUTHORITY_CHANGE,
POLICY_ACTIVATION, RISK_CONFIG_CHANGE, STRATEGY_PROMOTION,
MODEL_PROMOTION, PRODUCTION_CONFIG_CHANGE, EMERGENCY_RELEASE,
PRIVILEGED_RECOVERY.
