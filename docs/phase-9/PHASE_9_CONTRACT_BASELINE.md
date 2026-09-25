# PHASE 9 CONTRACT BASELINE (LOCKED)

All versions are the ACTUAL repository state at freeze time. Every
change made across Phases 7-9 was MINOR/BACKWARD; no Phase 0-6 contract
was broken.

## Registries

| contract | version | phase 9 change | compatibility |
|---|---|---|---|
| architecture.yaml | 1.9.0 | ui.desktop ACTIVE; platform.api facade | BACKWARD |
| manifest.yaml | phase 9 / system 1.0.0 | version sync | BACKWARD |
| schema-registry.yaml | 1.9.0 | +workspace_state (96 schemas) | BACKWARD |
| identifiers.yaml | 1.8.0 | (from Phase 8; +16 kinds) | BACKWARD |
| state-machines.yaml | 1.5.0 | (from Phase 8; 16 machines) | BACKWARD |
| permissions.yaml | 1.1.0 | (from Phase 8; +4 perms, SECURITY_ADMIN) | BACKWARD |
| events (python+schema) | 1.3.0 | (from Phase 8; +27 security types) | BACKWARD |
| audit_record | 1.1.0 | (from Phase 8; +integrity fields) | BACKWARD |
| platform.security | 1.1.0 | (from Phase 8) | BACKWARD |
| **workspace_state** | **1.0.0** | **NEW in Phase 9 (ui.desktop)** | new contract |

## workspace_state 1.0.0 (the only new contract)

- Owner: ui.desktop; binding ui.desktop.contracts:WorkspaceState.
- Fields: workspace_id, preset (enum of 7), panels[] (panel_id, region
  A-G, dock DOCKED/FLOATING/PINNED/COLLAPSED, size_weight (0,100],
  order_index, visible), monitor, schema_version.
- UI-ONLY by constraint: no domain field can exist on it; corrupted
  state recovers to the default A-G layout (tested).
- Consumers: WorkstationModel.persist_workspace/restore_workspace;
  the Tk shell renders it. No core engine reads it.
- Migration: none required (new contract).

## State machines (16)

system_state, market_state, risk_state, execution_state, order_state,
position_status, environment, policy_status, decision_status,
strategy_lifecycle, portfolio_status, model_lifecycle (P7),
credential_lifecycle, session_state, approval_flow, incident_lifecycle
(P8).

## Schema registry: 96 schemas

Phase 0-6: 60 · Phase 7: +23 · Phase 8: +12 · Phase 9: +1
(workspace_state). All generated/aligned with python bindings; the
drift test enforces field/required equality.

## Migration requirements

None. Every consumer compiled against 1.x earlier versions continues
to work: all changes were additive (new enum values, optional fields,
new schemas, new machines). The two Phase 0 completeness-registry test
expectation sets (event types, identifier kinds) and the validator rule
count were extended with each documented bump - strict equality is
preserved everywhere.
