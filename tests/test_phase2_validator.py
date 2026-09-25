"""Phase 2 validator rule tests: detection via corrupted project copies."""
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
    def test_real_project_passes_phase2_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()


class TestPhase2RuleDetection:
    def test_state001_missing_event_reference_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "state_record.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["source_event_id"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STATE-001")

    def test_state002_mutable_state_detected(self, project_copy):
        contracts = project_copy / "core" / "state" / "contracts.py"
        text = contracts.read_text(encoding="utf-8")
        text = text.replace(
            "@dataclass(frozen=True)\nclass StateRecord:", "@dataclass\nclass StateRecord:", 1
        )
        contracts.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STATE-002")

    def test_state003_snapshot_without_hash_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "state_snapshot.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["hash"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STATE-003")

    def test_ledger001_mutating_ledger_store_detected(self, project_copy):
        port = project_copy / "core" / "ledger" / "store.py"
        port.write_text(
            port.read_text(encoding="utf-8")
            + "\n    def delete_entries(self, account_id):\n        ...\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "LEDGER-001")

    def test_ledger004_float_amount_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "ledger_entry.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["amount"]["type"] = "number"
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "LEDGER-004")

    def test_ledger003_missing_idempotency_detected(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "ledger_entry.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        del schema["fields"]["idempotency_key"]
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "LEDGER-003")

    def test_recon001_auto_fix_detected(self, project_copy):
        engine = project_copy / "core" / "reconciliation" / "engine.py"
        engine.write_text(
            engine.read_text(encoding="utf-8")
            + "\n\ndef hidden_auto_fix(store, entries):\n    store.delete_entries('ACC-1')\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RECON-001")

    def test_recon003_hardcoded_tolerance_detected(self, project_copy):
        module = project_copy / "core" / "reconciliation" / "tolerance.py"
        text = module.read_text(encoding="utf-8")
        text = text.replace('load_registry("tolerances.yaml")', 'load_registry("currencies.yaml")')
        module.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RECON-003")

    def test_recon004_mutable_result_detected(self, project_copy):
        contracts = project_copy / "core" / "reconciliation" / "contracts.py"
        text = contracts.read_text(encoding="utf-8")
        text = text.replace(
            "@dataclass(frozen=True)\nclass ReconciliationResult:",
            "@dataclass\nclass ReconciliationResult:", 1,
        )
        contracts.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RECON-004")
