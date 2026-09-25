# Ledger Integrity (Phase 2)

- **WHAT**: Per-account hash chain + entry hashes + idempotency keys on every posted entry.
- **WHY**: Financial history must be verifiable end to end; tampering or accidental duplication must be detectable (SECTIONS 16-19).
- **SOURCE OF TRUTH**: the ledger store; entry hashes commit to the canonical entry content (amount/quantity as canonical decimal strings).
- **INPUT**: posted entries.
- **OUTPUT**: `entry_hash` (sha256 of canonical content), `previous_entry_hash` (per-account chain), `idempotency_key` (deterministic duplicate guard).
- **IMMUTABILITY**: the chain is append-only; each new entry must reference the current account head. The store REJECTS: wrong previous hash, duplicate idempotency key, missing hash/key, hash mismatch.
- **FAILURE**: `verify_account_chain(account)` walks the chain and reports ENTRY_HASH_MISMATCH / CHAIN_LINK_MISMATCH issues (empty list = intact). Forgery attempts fail closed at the storage boundary.
- **RECOVERY**: chains verify after restart; idempotent reposting cannot branch the chain.
- **VERSION**: ledger_entry 1.1.0; the hash algorithm is part of the contract - changing it requires a MAJOR version.
- **TEST**: `tests/test_phase2_stores_ledger.py` (chain build, forged-entry rejection, reversal keeps chain intact), `test_phase2_invariants_e2e.py`.
