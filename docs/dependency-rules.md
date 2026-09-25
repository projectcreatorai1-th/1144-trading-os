# Dependency Rules

- **WHAT**: The allowed dependency graph of the system, as data (not scattered assertions).
- **WHY**: Prevents architecture branching - no UI-to-database shortcuts, no AI-to-broker paths, no duplicated engines (RULE 001-008).
- **BOUNDARY**: Declared once in `architecture/architecture.yaml`; enforced by the Architecture Validator (registry checks + source import scan).

## Direction

```
UI (ui.desktop, ui.web)
  -> API layer (platform.api)
    -> Core (core.*)
      -> Infrastructure (platform.* except api) and Adapters (adapters.*)
Every module -> shared kernel (architecture.contracts)
```

## Hard prohibitions (from the specification)

| Rule | Violation | Enforced by |
|---|---|---|
| GF-001 | any non-UI module depending on ui.* | global rule + IMPORT-001 |
| GF-002 | UI -> database / event bus / adapters | ARCH-006 + import scan |
| GF-003 | AI -> MT5 / broker | ARCH-006 |
| GF-004 | strategy -> broker/MT5 adapters | ARCH-006 |
| GF-005 | core -> GUI frameworks (ui.*) | ARCH-006 |
| GF-006 | execution -> AI/news/calendar adapters | ARCH-006 |
| layer rule | upward dependency without declared port implementation | ARCH-005 |
| whitelist | dependency outside a module's allow-list | IMPORT-001 |

## Port implementation exception

Infrastructure modules may depend on core modules only for ports they declare in `implements` (dependency inversion). Phase 0 declarations: `platform.database` implements `core.events.EventRepository`, `core.execution.OrderRepository`, `core.ledger.LedgerRepository`, `core.portfolio.PositionRepository`, `platform.audit.AuditRepository`. The validator checks each declared port class exists.

## Database boundary (SECTION 33)

Core -> repository/service interface (port, e.g. `core.events.repository.EventRepository`) -> database adapter (`platform.database`). Core and GUI never touch SQL; the database technology can change without breaking core contracts.

- **INPUT**: `architecture.yaml` + the Python source tree.
- **OUTPUT**: structured violations (rule, location, message).
- **FAILURE**: violations fail validation and CI (validator exit code 1).
- **VERSION**: dependency policy 1.0.0.
- **TEST**: `tests/test_validator.py` (forbidden/unknown/upward dependencies, import violations), `tests/test_no_production_path.py`.
