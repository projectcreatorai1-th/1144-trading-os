# Authentication

AuthenticationResult carries actor, method, timestamp, environment,
credential reference, strength, status, expiry and failure reason.
States: AUTHENTICATED / UNAUTHENTICATED / EXPIRED / REVOKED / LOCKED /
UNKNOWN - fail closed.

Credentials store ONLY a salted PBKDF2-HMAC-SHA256 verifier (100k
iterations, stdlib; nothing invented). The secret never persists, never
logs, never enters audit payloads or error messages (the failure path
scrubs echoed material).

Credential lifecycle (state machine): ISSUED -> ACTIVE ->
ROTATION_REQUIRED -> ROTATED -> REVOKED/EXPIRED. Rotation creates a NEW
immutable version; the old credential never regains authority. Revoked
and expired credentials can never authenticate again.

Environment binding: a credential issued for one environment never
authenticates another.
