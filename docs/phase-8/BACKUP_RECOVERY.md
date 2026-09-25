# Backup + Restore Security

BackupManifest: backup_id, created_at, environment, source_version,
per-component content hashes (EventStore/StateStore/Ledger/Audit/
governance artifacts/configuration/security records), content_hash over
the components, manifest_hash over the manifest, integrity status.

- create_backup refuses empty component sets; verification recomputes
  every component hash.
- authorize_restore is explicit: integrity UNKNOWN or CORRUPTED ->
  REJECT/BLOCK (never restore blindly).
- Recovery preserves append-only history, state/ledger/audit integrity
  and governance evidence by reusing the existing deterministic
  rebuild/recovery mechanisms (no second reconstruction engine).
