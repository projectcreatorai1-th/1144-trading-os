# Failure-Closed Matrix (SECTION 41) — 27/27 PASS

| Condition | Result | Test |
|---|---|---|
| unauthenticated | BLOCK | row_unauthenticated |
| expired session | BLOCK | row_expired_session |
| revoked session | BLOCK | row_revoked_session |
| unknown actor | BLOCK | row_unknown_actor |
| unknown permission | BLOCK | row_unknown_permission |
| permission mismatch | BLOCK | row_permission_mismatch |
| environment mismatch | BLOCK | row_environment_mismatch |
| missing approval | BLOCK | row_missing_approval |
| self approval | BLOCK | row_self_approval |
| expired approval | BLOCK | row_expired_approval |
| revoked permission | BLOCK | row_revoked_permission |
| revoked credential | BLOCK | row_revoked_credential |
| malformed request | BLOCK | row_malformed_request |
| replayed request | idempotent evidence / BLOCK on execution | row_replayed_request |
| conflicting idempotency | BLOCK (corruption) | row_conflicting_idempotency |
| rate limit exceeded | BLOCK | row_rate_limit |
| secret exposure attempt | redacted | row_secret_exposure |
| audit integrity failure | BLOCK privileged | row_audit_integrity |
| backup UNKNOWN | BLOCK restore | row_backup_unknown |
| backup corrupted | BLOCK restore | row_backup_corrupted |
| AI self approval | BLOCK | row_ai_self_approval |
| AI self promotion | BLOCK | row_ai_self_promotion |
| AI direct order | impossible | row_ai_direct_order |
| AI direct RiskDecision | impossible | row_ai_direct_risk_decision |
| LIVE without permission | BLOCK | row_live_without_permission |
| LIVE without approval | BLOCK | row_live_without_approval |
| emergency control bypass | BLOCK | row_emergency_bypass |

UNKNOWN is never SAFE anywhere in the matrix.
