# Phase 8 Architecture

## Modules (architecture.yaml 1.8.0, both ACTIVE)

core/security: contracts (credential/session/key/secret/approval/request/
incident/backup/rate-limit/encryption-policy), authentication (PBKDF2
verifiers + session service), authorization (delegates to the canonical
registry), services (maker-checker), secrets (vault + keys + encryption
policy), protection (replay guard, rate limiter, audit chain, backup
security, incident service, security events, metrics).

core/governance: gate (GovernanceGate + GovernanceArtifact +
GovernanceTransition).

## Dependency boundaries

core.security allowed: architecture.contracts, core.events, core.time,
core.validation, platform.security, platform.audit. Forbidden: oms/ems/
execution/risk/policy/portfolio/strategy/intelligence/research/ledger/
state/adapters/ui.

core.governance allowed: + core.security. Forbidden: everything else in
core (it wraps existing lifecycles by evidence, not by import).

## Registries (REUSE -> EXTEND -> VERSION)

identifiers 1.8.0 (+8 kinds), state-machines 1.5.0 (+credential_lifecycle,
session_state, approval_flow, incident_lifecycle), events 1.3.0 (+27
security types), permissions 1.1.0 (+CONFIGURE/MANAGE_CREDENTIALS/
MANAGE_SECURITY/MANAGE_GOVERNANCE + SECURITY_ADMIN role), audit_record
1.1.0 (+integrity_hash/previous_hash), schema-registry 1.8.0 (+12 schemas
generated from bindings), manifest 0.9.0/phase 8.
