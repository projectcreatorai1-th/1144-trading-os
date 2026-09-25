# Versioning and Compatibility

- **WHAT**: MAJOR.MINOR.PATCH versioning for every contract/registry, plus automated compatibility rules.
- **WHY**: Contracts may change, but never silently and never breaking consumers unnoticed (RULE 015-016).
- **BOUNDARY**: Applies to all schemas in the schema registry and all registry files (`architecture.yaml`, `identifiers.yaml`, `state-machines.yaml`, `permissions.yaml`, `api.yaml`, `manifest.yaml`).

## Rules (SECTION 22)

| Change | Impact | Required bump |
|---|---|---|
| add optional field | compatible | MINOR |
| add required field | potentially breaking | MAJOR |
| remove field | breaking | MAJOR |
| change field type | breaking | MAJOR |
| add enum value | compatibility risk (consumers may not know it) | MINOR (flagged) |
| remove enum value | breaking | MAJOR |
| change semantic meaning | breaking | MAJOR |
| documentation only | compatible | PATCH |

## Enforcement

- `architecture.contracts.versioning` implements `SemVer`, `evaluate_changes` and `check_history`.
- Every schema's `history` lists each version with typed change entries; `check_history` rejects: silent changes, version regressions, non-increasing history, and breaking changes without a MAJOR bump (rule `SCHEMA-002`).
- The Architecture Validator re-runs these checks over the real registries, and the manifest pins every registry version (`MANIFEST-001`).
- Declared `compatibility: BACKWARD` combined with breaking history entries fails (`SCHEMA-003`).

## Procedure for changing a contract

1. Add a typed change entry (`kind`, field/enum/value) to the schema `history`.
2. Bump the schema version according to the table.
3. Update `schema-registry.yaml` index version and `architecture/manifest.yaml` contract version.
4. Update the Python binding in the same change.
5. Run `python -m pytest` and `python -m architecture.validator` - both must pass.

- **INPUT**: schema YAML + history entries.
- **OUTPUT**: validator/test verdicts.
- **DEPENDENCY**: kernel versioning module.
- **FAILURE**: mismatched bumps, silent changes, index/file version drift - all deterministic failures.
- **VERSION**: this policy is effective since contract version 1.0.0 (Phase 0).
- **TEST**: `tests/test_versioning.py`, `tests/test_validator.py` (breaking-change detection), `tests/test_schema_registry.py` (index/file drift).
