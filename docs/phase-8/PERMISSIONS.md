# Permissions (permissions.yaml 1.1.0)

Permissions: VIEW, ANALYZE, RESEARCH, SIMULATE, PAPER_TRADE, DEMO_TRADE,
LIVE_TRADE, MODIFY_POLICY, MODIFY_RISK, APPROVE, PAUSE, CLOSE_ONLY,
EMERGENCY_STOP, ADMIN + Phase 8: CONFIGURE, MANAGE_CREDENTIALS,
MANAGE_SECURITY, MANAGE_GOVERNANCE.

Roles: VIEWER, ANALYST, RESEARCHER, TRADER, RISK_MANAGER, OPERATOR,
APPROVER + Phase 8: SECURITY_ADMIN (security operations, no trading).

Environment restrictions (registry-enforced): LIVE_TRADE -> [LIVE],
DEMO_TRADE -> [DEMO], PAPER_TRADE -> [PAPER], SIMULATE -> research-plane
environments. A permission granted for one environment never applies to
another; there is no cross-environment fallback and DEMO never inherits
LIVE.

The permission snapshot in a session is evidence of what was frozen at
issue time; every authorize() call re-derives the answer from the
canonical registry, so registry-side revocation is immediately effective
(SEC-026/059).
