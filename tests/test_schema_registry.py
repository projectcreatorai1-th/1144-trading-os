"""Schema registry tests (SECTION 6) - registry-driven instance validation."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from architecture.contracts.errors import ContractError
from architecture.contracts.schema import build_schema_registry, supported_environments
from architecture.contracts.registry import find_project_root
from tests.factories import T0

REGISTRY = build_schema_registry()

REQUIRED_SCHEMAS = {
    "event", "decision", "policy", "risk_decision", "order", "position",
    "ledger_entry", "audit_record", "permission", "environment",
    "api_request", "api_response",
}


class TestRegistryStructure:
    def test_required_schemas_present(self):
        assert REQUIRED_SCHEMAS <= set(REGISTRY.schema_ids)

    @pytest.mark.parametrize("schema_id", sorted(REQUIRED_SCHEMAS))
    def test_schemas_are_versioned_and_owned(self, schema_id):
        schema = REGISTRY.get(schema_id)
        assert schema.version.count(".") == 2
        assert schema.owner
        assert schema.status == "ACTIVE"
        assert schema.fields
        assert schema.history

    def test_unknown_schema_id_is_hard_error(self):
        with pytest.raises(ContractError):
            REGISTRY.get("nonexistent_schema")


def _event_instance(**overrides):
    data = {
        "event_id": "evt_" + "0" * 32,
        "event_type": "ORDER_CREATED",
        "event_version": "1.0.0",
        "event_time": T0.isoformat(),
        "received_time": T0.isoformat(),
        "source": "core.execution",
        "source_id": "execution-engine",
        "environment": "PAPER",
        "correlation_id": "cor_" + "1" * 32,
        "causation_id": None,
        "entity_id": None,
        "payload": {"order_id": "ord_" + "2" * 32},
        "metadata": {},
    }
    data.update(overrides)
    return data


class TestInstanceValidationPasses:
    def test_valid_event_instance(self):
        assert REGISTRY.validate_instance("event", _event_instance()) == []

    def test_optional_fields_may_be_absent(self):
        data = _event_instance()
        data.pop("causation_id")
        data.pop("metadata")
        assert REGISTRY.validate_instance("event", data) == []


class TestInstanceValidationFailures:
    def test_missing_required_field(self):
        data = _event_instance()
        del data["correlation_id"]
        issues = REGISTRY.validate_instance("event", data)
        assert any(i.rule_id == "SCHEMA-REQUIRED" and i.location == "event.correlation_id" for i in issues)

    def test_none_required_field_is_missing(self):
        issues = REGISTRY.validate_instance("event", _event_instance(event_type=None))
        assert any(i.rule_id == "SCHEMA-REQUIRED" for i in issues)

    def test_invalid_enum(self):
        issues = REGISTRY.validate_instance("event", _event_instance(event_type="ORDER_TELEPORTED"))
        assert any(i.rule_id == "SCHEMA-ENUM" for i in issues)

    def test_invalid_type(self):
        issues = REGISTRY.validate_instance("event", _event_instance(payload=["not", "a", "map"]))
        assert any(i.rule_id == "SCHEMA-TYPE" for i in issues)

    def test_naive_timestamp_string_rejected(self):
        issues = REGISTRY.validate_instance("event", _event_instance(event_time="2026-09-23T12:00:00"))
        assert any(i.rule_id == "SCHEMA-TIMESTAMP" for i in issues)

    def test_invalid_timestamp_string_rejected(self):
        issues = REGISTRY.validate_instance("event", _event_instance(received_time="yesterday"))
        assert any(i.rule_id == "SCHEMA-TIMESTAMP" for i in issues)

    def test_unknown_environment_rejected(self):
        issues = REGISTRY.validate_instance("event", _event_instance(environment="PROD"))
        assert any(i.rule_id == "SCHEMA-ENVIRONMENT" for i in issues)

    def test_non_mapping_instance_rejected(self):
        issues = REGISTRY.validate_instance("event", ["not", "a", "mapping"])
        assert any(i.rule_id == "SCHEMA-TYPE" for i in issues)


class TestRegistryIntegrityFailures:
    def _copy_architecture(self, tmp_path: Path) -> Path:
        import shutil

        root = find_project_root()
        shutil.copytree(root / "architecture", tmp_path / "architecture")
        return tmp_path

    def test_duplicate_schema_id_detected(self, tmp_path):
        root = self._copy_architecture(tmp_path)
        index_path = root / "architecture" / "schema-registry.yaml"
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        index["schemas"].append(dict(index["schemas"][0]))
        index_path.write_text(yaml.safe_dump(index), encoding="utf-8")
        with pytest.raises(ContractError) as excinfo:
            build_schema_registry(project_root=str(tmp_path))
        assert "Duplicate schema id" in str(excinfo.value)

    def test_schema_file_missing_detected(self, tmp_path):
        root = self._copy_architecture(tmp_path)
        (root / "architecture" / "schemas" / "event.yaml").unlink()
        with pytest.raises(ContractError) as excinfo:
            build_schema_registry(project_root=str(tmp_path))
        assert "missing" in str(excinfo.value)

    def test_version_mismatch_between_index_and_file_detected(self, tmp_path):
        root = self._copy_architecture(tmp_path)
        index_path = root / "architecture" / "schema-registry.yaml"
        index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
        index["schemas"][0]["version"] = "9.9.9"
        index_path.write_text(yaml.safe_dump(index), encoding="utf-8")
        with pytest.raises(ContractError) as excinfo:
            build_schema_registry(project_root=str(tmp_path))
        assert "Version mismatch" in str(excinfo.value)


class TestEnvironmentSingleSource:
    def test_supported_environments_from_architecture_registry(self):
        assert supported_environments() == [
            "RESEARCH", "SIMULATION", "REPLAY", "PAPER", "DEMO", "LIVE", "BACKTEST"
        ]
