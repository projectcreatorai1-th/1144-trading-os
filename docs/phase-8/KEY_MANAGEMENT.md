# Key Management

KeyRecord: key_id, version, purpose (AUDIT_INTEGRITY / SECRET_ENVELOPE /
REQUEST_SIGNING), environment, status (ACTIVE/ROTATED/REVOKED), created/
rotated/revoked metadata. Rotation bumps the version; revoked keys
record revoked_at; active_for() fails closed when no active key exists
for a purpose/environment.

One canonical cryptographic boundary: stdlib primitives only (PBKDF2-
HMAC-SHA256, HMAC-SHA256, SHA-256). No algorithms were invented.
