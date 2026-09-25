# Architecture

- **WHAT**: The 1144 Trading OS is a contract-first trading operating system. Phase 0 delivers its architecture registry, schema registry, versioned contracts, state machines, validator and tests - not a trading system.
- **WHY**: Every later phase (data, strategy, policy, risk, execution, MT5, AI, GUI) must build on one foundation so business logic is never duplicated and no parallel calculation system can appear (RULE 001-003).
- **BOUNDARY**: Phase 0 implements contracts and validation only. No engine, no broker connection, no AI behavior, no GUI beyond contract scope.

## Layers (dependency direction: top may depend on bottom)

| Order | Layer | Contains |
|---|---|---|
| 0 | contracts_kernel | `architecture.contracts` (shared kernel), `architecture.validator` |
| 1 | ui | `ui.desktop`, `ui.web` |
| 2 | api | `platform.api` |
| 3 | core | `core.*` (data, time, events, intelligence, research, strategy, decision, policy, risk, portfolio, execution, reconciliation, ledger, validation) |
| 4 | infrastructure | `platform.*` (database, event_bus, cache, scheduler, security, audit, monitoring, incident, recovery, backup) and `adapters.*` |

Rules:

- A module may depend on the shared kernel, on lower layers, and on its own allow-list.
- Infrastructure may depend on core ONLY to implement declared core ports (`implements` in the registry - dependency inversion).
- The UI talks only to `platform.api` - never to database, event bus or adapters.
- AI adapters never depend on broker/MT5 adapters; execution never consumes AI adapters directly.

## Areas

`Core`, `Platform`, `Adapters`, `UI`, `Research`, `Tests`, `Docs`, `Architecture` - exactly the tree in `architecture/architecture.yaml`, which is the single source of truth (RULE 001).

- **INPUT**: registries under `architecture/` (YAML).
- **OUTPUT**: validated contracts (Python), validation results (structured).
- **DEPENDENCY**: none outside the project; PyYAML only.
- **FAILURE**: any structural registry inconsistency, dependency violation or contract violation is a deterministic, structured failure (fail closed, RULE 019).
- **VERSION**: architecture registry 1.0.0; each module status is ACTIVE or SKELETON in the registry.
- **TEST**: `tests/test_validator.py`, `tests/test_manifest_structure.py`, `python -m architecture.validator`.

## Files that are the source of truth

| File | Owns |
|---|---|
| `architecture/architecture.yaml` | modules, layers, dependency policy, environments, rules index |
| `architecture/schema-registry.yaml` + `architecture/schemas/` | data contracts |
| `architecture/identifiers.yaml` | global ID standard |
| `architecture/state-machines.yaml` | all states and transitions |
| `architecture/permissions.yaml` | role/permission matrix |
| `architecture/api.yaml` | endpoints and reserved namespaces |
| `architecture/manifest.yaml` | versions and structure consistency |
