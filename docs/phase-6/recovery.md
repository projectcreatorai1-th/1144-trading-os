# Recovery (Phase 6)

- **WHAT**: Crash model: research runs are deterministic functions of immutable inputs.
- **WHY**: Restart must not duplicate or diverge financial effects (SECTION 95-97).
- **BOUNDARY**: No cross-store atomicity claims.
- **SOURCE OF TRUTH**: Deterministic run identity (content hashes).
- **ASSUMPTIONS**: 'Crash' = interrupted process; re-run is the recovery.
- **FAILURE**: Corrupted dataset/artifact/checkpoint hash => fail closed, never resume.
- **RECOVERY**: Re-run converges to the identical result.
- **REPRODUCIBILITY**: Same identity before and after restart.
- **TEST**: E2E-015
