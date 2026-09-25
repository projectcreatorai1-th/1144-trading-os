# Strategy Contract (Phase 4)

- **WHAT**: Strategy contract (schema strategy_record 1.0.0): identity, lifecycle, environment, config/capability/policy/budget references.
- **WHY**: Deterministic strategy identity with LIVE prerequisites (policy + risk budget references required).
- **SOURCE OF TRUTH**: architecture/schemas/strategy_record.yaml.
- **INPUT**: Strategy document fields.
- **OUTPUT**: Frozen validated Strategy instances.
- **IMMUTABILITY**: Frozen dataclass; versions unique per (id, version) in the store.
- **FAILURE**: LIVE without prerequisites fails closed (STRATEGY-002); bad enums/ids rejected.
- **RECOVERY**: Historical versions queryable forever (iter_versions).
- **VERSION**: 1.0.0.
- **TEST**: TestStrategyContract in tests/test_phase4_strategy.py
