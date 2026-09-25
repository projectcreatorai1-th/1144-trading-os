# Decision Trace (Causality and Provenance)

- **WHAT**: correlation_id + causation_id link every object into causal chains; provenance links decisions to their data/model/policy sources.
- **WHY**: The full chain NEWS -> EVENT -> MARKET STATE -> AI -> STRATEGY -> POLICY -> RISK -> ORDER -> EXECUTION -> POSITION -> RESULT must stay reconstructible (RULE 018, SECTIONS 23-24).
- **BOUNDARY**: Phase 0 provides the contract and chain validator; trace UI arrives later.

## Chain rules (enforced by `architecture.contracts.causality.validate_causal_chain`)

1. Every node carries a non-empty correlation_id; one chain shares exactly one correlation_id.
2. causation_id is either None (exactly one chain root) or a node id present in the chain.
3. Causation cycles are rejected; duplicate node ids are rejected.

Node identity attributes recognized: `event_id`, `decision_id`, `risk_decision_id`, `order_id`, `position_id`, `ledger_entry_id`, `audit_id`, `id`.

## Provenance (on decision-critical objects)

`Provenance`: source, source_id, source_version, event_time, ingestion_time (>= event_time), processing_time, model_version, policy_version, data_version.
Decisions REQUIRE provenance (`PROV-001`); risk decisions reference their policy (`policy_reference`); AI analyses carry model_version ids from the model registry.

## Pipeline stages prepared

RAW -> NORMALIZED -> ANALYZED -> DECISION -> EXECUTION -> RESULT - each stage's objects carry correlation_id/causation_id so the chain can be walked in both directions. `tests/test_trace_chain.py` proves the whole chain end-to-end in Phase 0.

- **INPUT**: any chain of contract objects.
- **OUTPUT**: validation verdict (or structured `CausalityError`).
- **DEPENDENCY**: kernel causality module.
- **FAILURE**: broken/missing links fail deterministically - a decision without provenance or a chain without a root is invalid, not warned.
- **VERSION**: trace contract 1.0.0.
- **TEST**: `tests/test_provenance_causality.py`, `tests/test_trace_chain.py`, `tests/test_decision.py`.
