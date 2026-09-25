# Execution Security (Phase 5)

- **WHAT**: Credential handling (deployment secret store only), permission model for LIVE, audit trails, no credential logging.
- **WHY**: Secrets never in source/logs; LIVE requires explicit permission (SECTION 45/46; INV-019/034).
- **BOUNDARY**: Validator rule SECX-001 blocks credential assignment in adapter source; AIX-001 blocks AI execution.
- **SOURCE OF TRUTH**: Phase 0 permission registry + deployment secret store (outside source control).
- **INPUT**: Actor roles for LIVE submission.
- **OUTPUT**: Permission denials audited.
- **FAILURE BEHAVIOR**: LIVE without LIVE_TRADE permission -> REJECT + AUDIT.
- **RECOVERY**: n/a.
- **AUDIT**: Permission denials audited (SECTION 34 list).
- **TEST**: boundary tests + validator SECX/AIX corruption tests
