# Contracts

- **WHAT**: Versioned data contracts for every important object of the system.
- **WHY**: Contracts are the language between modules; they make behavior testable and changes detectable (RULE 015-016).
- **BOUNDARY**: Contracts validate; they do not calculate business results (no P&L math, no risk metrics computation - that is later phases' business logic).

## Registry

Index: `architecture/schema-registry.yaml`. Definitions: `architecture/schemas/<schema_id>.yaml`.
Each schema declares `schema_id, version, status, owner, fields, enums, constraints, compatibility, history` and a `python_binding`. Tests prove binding and schema never drift (`tests/test_schema_drift.py`).

## Contracts in Phase 0 (all v1.0.0)

| Schema | Owner | Python binding | Notes |
|---|---|---|---|
| event | core.events | `core.events.contracts:Event` | immutable; correlation/causation chain |
| decision | core.decision | `core.decision.contracts:Decision` | requires provenance; never authorizes execution |
| policy | core.policy | `core.policy.contracts:Policy` | ACTIVE requires approver |
| risk_decision | core.risk | `core.risk.contracts:RiskDecision` | explicit result; UNKNOWN fails closed; expires |
| order | core.execution | `core.execution.contracts:Order` | lifecycle; risk gate on SUBMITTED |
| position | core.portfolio | `core.portfolio.contracts:Position` | multi-strategy per account |
| ledger_entry | core.ledger | `core.ledger.contracts:LedgerEntry` | immutable; corrections via linked entries |
| audit_record | platform.audit | `platform.audit.contracts:AuditRecord` | who/what/when/before/after/why/versions |
| permission | platform.security | `platform.security.contracts:PermissionGrant` | matrix in `permissions.yaml` |
| environment | architecture.contracts | `architecture.contracts.environment:EnvironmentDeclaration` | explicit enum; mismatch fails closed |
| api_request | platform.api | `platform.api.contracts:APIRequest` | endpoint + permission + environment |
| api_response | platform.api | `platform.api.contracts:APIResponse` | structured errors; contract versions |
| api | platform.api | - | endpoint registry contract |
| provenance | architecture.contracts | `architecture.contracts.provenance:Provenance` | source chain for decisions |
| data_quality | core.validation | `core.validation.contracts:DataQualityReport` | UNKNOWN/DEGRADED block risk allowance |
| config | architecture.contracts | `architecture.contracts.config:ConfigContract` | separated, versioned; no secrets |

## Common guarantees

- Every instance carries (as applicable): id, type/status, version, timezone-aware timestamp(s), source, correlation_id, causation_id, environment, provenance.
- All instances are immutable frozen dataclasses; corrections create new linked objects.
- `validate()` raises structured `ContractError`s (rule_id, location, message, details) - never returns a bare boolean.

- **INPUT**: constructor arguments / plain dicts (schema engine `validate_instance`).
- **OUTPUT**: validated instances or structured failures.
- **DEPENDENCY**: all contracts depend only on `architecture.contracts` (kernel).
- **FAILURE**: missing required fields, invalid enums, naive timestamps, unknown environments, broken trace references - all deterministic failures.
- **VERSION**: see schema files; history per schema; breaking changes raise MAJOR (see docs/versioning.md).
- **TEST**: `tests/test_events.py`, `test_decision.py`, `test_policy.py`, `test_risk.py`, `test_order.py`, `test_position.py`, `test_ledger.py`, `test_audit.py`, `test_permission.py`, `test_api.py`, `test_config.py`, `test_schema_registry.py`, `test_schema_drift.py`.

## Contract change log

| Date | Contract | Version | Change |
|---|---|---|---|
| 2026-09-23 | all | 1.0.0 | initial Phase 0 contracts |
| 2026-09-23 | event | 1.1.0 | +7 Phase 1 foundation event types (enum additions) |
| 2026-09-23 | data_quality | 1.1.0 | +STALE, INVALID quality states (Phase 1 classification) |
| 2026-09-23 | risk_decision | 1.1.0 | data_quality_level enum mirrors STALE/INVALID (fail closed) |
| 2026-09-23 | identifiers registry | 1.1.0 | +raw_id, normalized_id, lineage_id, ingestion_id |
| 2026-09-23 | schema registry | 1.1.0 | +9 schemas: raw_data, normalized_data, data_source, lineage_record, quality_check, latency_report, market_tick, news_item, calendar_event |
| 2026-09-23 | architecture registry | 1.1.0 | core.data/core.time/core.validation/platform.event_bus/platform.monitoring activated with Phase 1 responsibilities; implements/allow-lists extended; benchmarks module registered |
| 2026-09-23 | ledger_entry | 1.1.0 | Phase 2: +quantity/symbol/status, integrity hash chain, idempotency key |
| 2026-09-23 | identifiers registry | 1.2.0 | +state_id, snapshot_id, transition_id, observation_id, reconciliation_id |
| 2026-09-23 | schema registry | 1.2.0 | +6 Phase 2 schemas (state_record, state_transition, state_snapshot, external_observation, reconciliation_result, difference) |
| 2026-09-23 | architecture registry / manifest | 1.2.0 | core.state module; core.ledger/core.reconciliation/platform.database activated; currencies + tolerances registries added |
| 2026-09-23 | policy | 1.1.0 | Phase 3: REVIEW/APPROVED lifecycle, 12 risk policy types, identity fields |
| 2026-09-23 | risk_decision | 1.2.0 | Phase 3: action identity, rule evidence, context/policy hashes, LIMITED constraints, provenance |
| 2026-09-23 | state contracts | 1.1.0 | Phase 3: RISK_STATE category (rides Phase 2 machinery, Phase 0 machine) |
| 2026-09-23 | identifiers / schema / architecture / manifest | 1.3.0 | Phase 3: +3 ids, +3 schemas (policy_evaluation, risk_context, risk_budget), risk-config registry |
| 2026-09-24 | state-machines | 1.2.0 | Phase 4: strategy_lifecycle + portfolio_status machines |
| 2026-09-24 | identifiers / schema / architecture / manifest | 1.4.0 | Phase 4: +6 ids, +15 schemas (strategy_*, portfolio_*, capital_allocation, capacity, liquidity_budget), core.strategy/core.portfolio activated |
