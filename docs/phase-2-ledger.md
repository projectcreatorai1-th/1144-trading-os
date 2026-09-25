# Phase 2 — Ledger

- **WHAT**: Immutable ledger engine: posting, idempotency, hash chain, money policy, balance reconstruction (`core.ledger`).
- **WHY**: Financial/accounting facts must be recorded without ever editing history; every number must trace to a transaction (SECTIONS 13-20).
- **SOURCE OF TRUTH**: Posted ledger entries are the accounting truth for balances; `LEDGER_POSTED` events make balances event-sourceable. Money semantics come from `architecture/currencies.yaml` (the only rounding authority).
- **INPUT**: `LedgerDraft` (type, account, decimal amount, currency, refs) + the source event.
- **OUTPUT**: posted `LedgerEntry` (contract 1.1.0: +quantity, symbol, status, entry_hash, previous_entry_hash, idempotency_key) + audit record + `LEDGER_POSTED` event.
- **IMMUTABILITY**: posted entries can never be updated or deleted; corrections are REVERSAL/ADJUSTMENT entries referencing the original (which remains, byte-identical). Enforced at the storage boundary (LEDGER-001/002).
- **FAILURE**: binary floats rejected (LEDGER-004), unknown currencies rejected, duplicate idempotency keys rejected (LEDGER-003), broken hash chains rejected.
- **RECOVERY**: idempotent posting - reprocessing the same event returns the existing canonical entry; hash chains verify per account (`verify_account_chain`).
- **VERSION**: ledger_entry schema 1.1.0 (non-breaking: six optional fields; documented history).
- **TEST**: `tests/test_phase2_stores_ledger.py`, `test_phase2_money.py`.

## Idempotency key

sha256 over (source_event_id, entry_type, account_id, currency, amount, semantic_ref) - deterministic, never timestamps or random values (SECTION 18).

## Money

`Money` = Decimal + registered currency (precision + rounding from currencies.yaml; default ROUND_HALF_EVEN). Cross-currency arithmetic is refused (FX is a later-phase interface boundary). JPY precision 0, USD/EUR/XAU precision 2.

## Balance reconstruction

opening + entries = closing, per account AND currency (multi-account by design; never one global balance), with per-type buckets (cash/execution/fee/commission/swap/funding/adjustment/transfer). Deterministic Decimal arithmetic; zero float involvement.
