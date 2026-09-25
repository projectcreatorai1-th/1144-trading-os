"""Schema drift tests: Python contract bindings must match the schema registry.

Every schema with a python_binding must have:
- exactly the same field names as the dataclass
- required-in-schema == no-default-in-python
This is the automated no-contract-drift gate (RULE 015/016).
"""
from __future__ import annotations

import dataclasses
import importlib

import pytest

from architecture.contracts.schema import build_schema_registry

REGISTRY = build_schema_registry()


def _binding_class(binding: str):
    module_path, _, class_name = binding.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


BOUND_SCHEMAS = sorted(
    entry["schema_id"]
    for entry in (
        REGISTRY.index_entry(schema_id)
        for schema_id in REGISTRY.schema_ids
    )
    if entry.get("python_binding")
)


@pytest.mark.parametrize("schema_id", BOUND_SCHEMAS)
def test_binding_matches_schema_fields(schema_id):
    schema = REGISTRY.get(schema_id)
    binding = REGISTRY.index_entry(schema_id)["python_binding"]
    cls = _binding_class(binding)
    assert dataclasses.is_dataclass(cls), f"{binding} must be a dataclass"
    dataclass_fields = {f.name: f for f in dataclasses.fields(cls)}
    schema_fields = schema.fields

    missing_in_python = sorted(set(schema_fields) - set(dataclass_fields))
    extra_in_python = sorted(set(dataclass_fields) - set(schema_fields))
    assert not missing_in_python, f"{schema_id}: schema fields missing in python binding: {missing_in_python}"
    assert not extra_in_python, f"{schema_id}: python binding fields missing in schema: {extra_in_python}"

    for name, spec in schema_fields.items():
        required_in_schema = bool(spec.get("required", False))
        has_default = dataclass_fields[name].default is not dataclasses.MISSING or \
            dataclass_fields[name].default_factory is not dataclasses.MISSING
        if required_in_schema:
            assert not has_default, f"{schema_id}.{name}: required in schema but optional in python"
        else:
            assert has_default, f"{schema_id}.{name}: optional in schema but required in python"


def test_binding_enums_match_schema_enums():
    """Enum values referenced by schema field types must equal the python enum."""
    from architecture.contracts.environment import Environment
    from core.events.contracts import EventType

    schema = REGISTRY.get("event")
    assert set(schema.enums["event_type"]) == {e.value for e in EventType}
    env_schema = REGISTRY.get("environment")
    assert set(env_schema.enums["environment"]) == {e.value for e in Environment}


def test_every_schema_owner_is_module_with_binding():
    for schema_id in REGISTRY.schema_ids:
        entry = REGISTRY.index_entry(schema_id)
        assert entry["owner"], f"{schema_id} must declare an owner"
        assert entry["status"] == "ACTIVE"
