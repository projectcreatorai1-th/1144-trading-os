# Secrets

SECRET VALUE != AUDIT EVIDENCE. Values live only inside the SecretVault
(encrypted at rest with a vault key derived via PBKDF2); everything else
holds SecretReference handles: id, purpose, environment, digest,
rotation lineage, revocation flag.

- Retrieval is by reference; revoked secrets are gone forever; UNKNOWN
  never yields a value.
- Rotation issues a NEW reference and retires the old one.
- Every log/audit/event path funnels through redaction
  (redact_payload scrubs password/token/api_key/secret/private_key/
  credential/session_secret keys; emit_security_event applies it).
- Credentials store only PBKDF2 verifiers (see AUTHENTICATION).
