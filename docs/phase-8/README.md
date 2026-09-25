# Phase 8 — Security + Governance + Audit

Guardrail plane around the existing authority chain
(NEWS -> INTELLIGENCE -> STRATEGY -> PORTFOLIO -> RISK -> RISK DECISION ->
OMS -> EMS -> MT5). Security authenticates and authorizes; governance
controls privileged change; audit preserves tamper-evident evidence.

None of the existing authorities was replaced: one permission source
(platform.security + permissions.yaml), one audit port (platform.audit,
extended with a chained-integrity layer), one risk engine, one OMS/EMS.

Key documents: SECURITY_MODEL, AUTHENTICATION, AUTHORIZATION,
PERMISSIONS, APPROVAL_GOVERNANCE, SECRETS, KEY_MANAGEMENT, AUDIT,
AUDIT_INTEGRITY, ENVIRONMENT_SECURITY, AI_SECURITY_BOUNDARY,
INCIDENT_RESPONSE, BACKUP_RECOVERY, FAILURE_MATRIX, TEST_REPORT,
VALIDATOR_REPORT, RELEASE_REPORT.
