# 1144 Trading OS - Phase 5 Report (OMS + EMS + MT5 ADAPTER)

Date: 2026-09-24. Built on Phase 0-4 (REUSE -> EXTEND -> VERSION): order state machine, risk decision validator, permission model, event store, state engine, ledger posting, reconciliation engine - all reused, never duplicated. NO Phase 6 work.

## 1. Scope

Execution boundary: canonical Order (contract 1.1.0) -> OMS (lifecycle/idempotency/cancel/replace) -> EMS (routing/timeout/UNKNOWN/retry/outbox) -> ExecutionAdapter port -> MT5 adapter (DEMO/LIVE mapping+normalization) + Simulation adapter -> immutable ExecutionReports -> position projection (Phase 2 state engine) -> ledger integration (Phase 2 posting, idempotent + hash-chained) -> reconciliation (Phase 2 engine, report-only). No strategy/portfolio/AI logic; no order was ever sent to a real broker from this codebase (MT5 terminal transport activates only at deployment with credentials from the secret store).

## 2. Architecture changes (all versioned, non-breaking)

- order schema 1.0.0 -> 1.1.0: +12 optional execution fields (causal chain intent_id/portfolio_decision_id/risk hashes/policy_version, idempotency_key, expires_at, order_version, provenance, causal_chain, replaces_order_id, account_id) + 12 execution lifecycle states
- state-machines registry 1.2.0 -> 1.3.0: order_state execution chain (CREATED->VALIDATING->ACCEPTED->ROUTING->SENT->ACKNOWLEDGED->PARTIALLY_FILLED->FILLED + REJECTED/CANCEL_REQUESTED/CANCELLED/EXPIRED/REPLACEMENT_REQUESTED/REPLACED/FAILED/UNKNOWN paths with broker-driven transitions and UNKNOWN recovery edges); position_status self-transitions + reopen
- identifiers registry 1.4.0 -> 1.5.0 (+outbox_message_id)
- 3 new schemas 1.0.0: execution_report, execution_capability, outbox_message
- architecture/manifest/schema registries 1.5.0; storage schema 2.3.0 -> 2.4.0 (+orders/execution_reports/outbox tables)
- new modules: core.oms (engine/contracts/stores/projection/reports_source), core.ems (engine/adapter/outbox), adapters.simulation (execution/sequencer); adapters.mt5 extended (execution/transport); core.execution extended (boundary/store/ports)
- validator extended to 94 rules (+OMS-001/002, EMS-001/002, EXEC-001..005, MT5-001/002, ENVX-001, POSX-001, LEDX-001, RECX-001, SECX-001, AIX-001, BOUNDX-001), each with corruption tests

## 3. Order contract + state machine

Orders carry the full causal chain (intent -> portfolio -> risk hashes) and NEVER derive from signals directly (SECTION 3). All lifecycle transitions run the Phase 0 machine engine: append-only versions, event/reason/actor/version per transition, invalid jumps fail closed.

## 4. Safety boundary (SECTION 36)

`can_execute(order, risk_decision, execution_context)` is the single deterministic gate: schema, environment, risk decision validity/expiry/hashes/subject/LIMITED constraints, order expiry, market state (UNKNOWN->BLOCK), safety controls (PAUSE/CLOSE_ONLY/EMERGENCY), LIVE permission. Returns ALLOW/REJECT/UNKNOWN(=BLOCK). No second execution permission engine exists (validator EXEC-005).

## 5-6. OMS + EMS

OMS owns WHAT ORDER EXISTS: validation (single OrderValidator shared with EMS via the boundary), immutable versioned persistence, deterministic idempotency (semantic key -> one canonical order -> no duplicate external submission), cancel with mandatory broker evidence, replacement as new immutable version requiring risk revalidation when material. EMS owns HOW: adapter-by-environment resolution (missing adapter fails closed, never cross-env), capability gating (UNKNOWN semantics block), timeout/connection/unknown outcomes -> UNKNOWN (never FAILED without evidence), deterministic retry classification (RETRYABLE/NON_RETRYABLE/UNKNOWN), durable outbox, route audit.

## 7-8. Adapters

ExecutionAdapter port + AdapterCapability (provenance-required, versioned). MT5 adapter: canonical->MT5 mapping, MT5 response->canonical taxonomy (numeric retcodes mapped; raw evidence preserved; MT5 objects never leak - validator MT5-001); terminal transport is a port whose MetaTrader5 implementation activates only with package+terminal at deployment; binds explicitly to DEMO or LIVE. Simulation adapter: real production adapter for SIMULATION with explicit synthetic-execution evidence (never called Demo).

## 9-11. Execution reports + positions + ledger

ExecutionReports immutable (corrections = linked CORRECTION reports). Fill ingestion: idempotent per deterministic per-broker-fill key, cumulative fills tracked exactly, overfill -> UNKNOWN corruption path (never clipped). Confirmed fills project positions through the Phase 2 state engine (POSITION_STATE; OPEN->PARTIALLY_CLOSED->CLOSED + top-up self-transitions) and post EXECUTION entries through the Phase 2 ledger (decimal, hash-chained, idempotent via semantic_ref = execution key; duplicate reports create no duplicate ledger effect). Duplicate execution dedup at projection level too (execution identity scan).

## 12-13. Reconciliation + recovery

Execution reconciliation reuses the Phase 2 engine (positions vs broker observations; MISMATCH reported, state untouched). Recovery = idempotency + outbox + reconciliation + replay; crash scenarios tested (before/after order persistence, before fill persistence, restart, duplicate recovery); no cross-system atomicity claimed.

## 14-16. Security + environments + observability

Credentials only in the deployment secret store (validator SECX-001 blocks credential assignment in adapter source; INV-034 tested). Cross-environment submission structurally impossible (all 6 pairs tested, E2E-014). Environment isolation enforced at EMS adapter resolution + boundary + projection. LIVE requires LIVE_TRADE permission. Connection state never implies order state (INV-036).

## Tests (real run, 2026-09-24, `python -m pytest`)

- Phase 0 Regression: **637/637**
- Phase 1 Regression: **157/157**
- Phase 2 Regression: **119/119**
- Phase 3 Regression: **103/103**
- Phase 4 Regression: **101/101**
- Phase 5 Tests: **130/130**
- **Total: 1247 passed / 0 failed / 0 skipped / 0 errors**
- Failure Tests (selection incl. overfill/timeout): **551/551** (selection; the full failure matrix classes all pass)
- Invariants: all 40 of SECTION 60 pass
- E2E: all 14 flows of SECTION 65 pass (SIMULATION full chain incl. position+ledger+reconciliation; DEMO MT5 mapping; risk block; expired risk; duplicate; partial fills; broker reject; timeout reconciliation; restart; close-only; cancel evidence; replace; mismatch no-autofix; 6-pair environment matrix)

## Validator

`python -m architecture.validator` -> **PASS (0 failures, 0 warnings)** with 94 rules; 18 new Phase 5 rules each proven by corruption detection (`tests/test_phase5_validator.py`).

## Contract changes (all versioned, non-breaking)

| Contract | Old | New | Reason |
|---|---|---|---|
| order | 1.0.0 | 1.1.0 | execution lifecycle states, causal chain, idempotency, expiry, replacement |
| state-machines registry | 1.2.0 | 1.3.0 | execution order chain + broker transitions; position self-transitions |
| identifiers registry | 1.4.0 | 1.5.0 | +outbox_message_id |
| schema registry | 1.4.0 | 1.5.0 | +3 schemas (execution_report, execution_capability, outbox_message) |
| architecture / manifest | 1.4.0 | 1.5.0 | core.oms/core.ems/adapters.simulation activation, ports, validator rules |
| storage schema | 2.3.0 | 2.4.0 | +orders/execution_reports/outbox tables |

## Quality audit (SECTION 71)

Repository scan (TODO/FIXME/mock/placeholder/fake/bypass/silent-except/utcnow/time.time/float-money/credential literals): clean except the known intentional Phase 0 fail-closed enum-parse. No duplicate risk engine (EXEC-005 corruption test), no direct MT5 domain dependency (MT5-001), no cross-environment execution (6-pair matrix), no unversioned contract, no silent clipping (overfill -> UNKNOWN), no auto-fix reconciliation (INV-029), no AI execution authority (AIX-001).

## Critical gap audit (SECTION 74)

| id | description | class |
|---|---|---|
| GAP-014 | Real MT5 terminal connectivity (package + terminal + credentials at deployment) | DEFERRED TO DEPLOYMENT by design - transport port + implementation shipped; cannot run a terminal in this environment |
| GAP-015 | TCA / slippage analytics | PHASE 6 (deferred) |
| GAP-016 | Statistical correlation / backtest / ML | PHASE 6 (deferred; BOUNDX-001 blocks early implementation) |

No CRITICAL gaps (no risk bypass, no duplicate-execution path, no environment leakage, no ledger/position corruption path, no unknown-treated-as-safe, no secret leakage).

## Known limitations

- MT5 terminal transport requires the MetaTrader5 package + running terminal + deployment credentials (implementation shipped, environment-gated); all MT5 logic tested against a controlled transport at the port boundary.
- Single-process, single-writer SQLite storage (documented since Phase 2).
- EMS retry policy classifies; a bounded retry executor loop is deployment wiring (classification + idempotency guarantees safety in the meantime).
- Local benchmark only.

## Benchmark (local, docs/phase-5/phase-5-benchmark.md)

100 iterations: OMS submit ~16.1 ms, EMS routing ~0.0 ms, fill ingest ~23.3 ms, projection (state+ledger) ~58.5 ms, full pipeline ~10.2 ops/s, recovery scan ~0.0 ms, 100 ledger entries. LOCAL DEVELOPMENT BENCHMARK - not production throughput.

========================================
1144 TRADING OS

PHASE 5 — OMS + EMS + MT5 ADAPTER

Phase 0 Regression: PASS (637/637)
Phase 1 Regression: PASS (157/157)
Phase 2 Regression: PASS (119/119)
Phase 3 Regression: PASS (103/103)
Phase 4 Regression: PASS (101/101)

Phase 5 Tests: PASS (130/130)
Failure Tests: PASS (551/551)
Invariant Tests: PASS (40/40)
Architecture Validator: PASS (94 rules, 0 failures)
Contract Validation: PASS
Replay: PASS (read-only, deterministic)
Recovery: PASS (crash scenarios idempotent)
Idempotency: PASS (order/fill/ledger)
Environment Isolation: PASS (6/6 pairs)
Security Audit: PASS
E2E: PASS (14/14)

Critical Gaps: 0

PHASE 5: PASS
READY FOR PHASE 6: YES

STOP.
DO NOT START PHASE 6.
========================================
