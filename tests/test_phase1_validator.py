"""Phase 1 validator rule tests: detection via deliberately corrupted copies."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from architecture.contracts.registry import find_project_root
from architecture.validator import run_architecture_validation
from architecture.validator.result import ValidationStatus

PROJECT_ROOT = find_project_root()


@pytest.fixture()
def project_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "proj"
    shutil.copytree(PROJECT_ROOT, destination)
    return destination


def _rules(result, rule_id: str) -> list:
    return [i for i in result.items if i.rule_id == rule_id]


class TestRealProject:
    def test_real_project_passes_phase1_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()
        assert {i.rule_id for i in result.items} == set()


class TestPhase1RuleDetection:
    def test_data001_mutating_store_port_detected(self, project_copy):
        port = project_copy / "core" / "data" / "stores.py"
        port.write_text(
            port.read_text(encoding="utf-8")
            + "\n    def update_raw(self, raw_id, payload):\n        ...\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "DATA-001")

    def test_data002_normalization_losing_provenance_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "normalized_data.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["provenance"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "DATA-002")

    def test_data003_sqlite_outside_database_module_detected(self, project_copy):
        sneaky = project_copy / "core" / "data" / "direct_sql.py"
        sneaky.write_text("import sqlite3\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "DATA-003")

    def test_time001_wrong_timestamp_type_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "raw_data.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["received_time"]["type"] = "string"
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "TIME-001")

    def test_time002_naive_datetime_hazard_detected(self, project_copy):
        hazard = project_copy / "core" / "time" / "hazard.py"
        hazard.write_text(
            "from datetime import datetime\n\n\ndef bad():\n    return datetime.utcnow()\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "TIME-002")

    def test_event003_non_frozen_record_detected(self, project_copy):
        contracts = project_copy / "core" / "data" / "contracts.py"
        text = contracts.read_text(encoding="utf-8")
        text = text.replace(
            "@dataclass(frozen=True)\nclass RawDataRecord:",
            "@dataclass\nclass RawDataRecord:",
            1,
        )
        contracts.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EVENT-003")

    def test_event004_missing_replay_gate_detected(self, project_copy):
        replay = project_copy / "core" / "events" / "replay.py"
        text = replay.read_text(encoding="utf-8")
        text = text.replace("assert_same_environment", "_unused_gate")
        replay.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EVENT-004")

    def test_event005_second_event_store_detected(self, project_copy):
        duplicate = project_copy / "core" / "data" / "store_copy.py"
        duplicate.write_text(
            "from abc import ABC\n\n\nclass EventStore(ABC):\n    pass\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EVENT-005")
