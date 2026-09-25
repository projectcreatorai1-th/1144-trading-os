"""Phase 3 validator rule tests: detection via corrupted project copies."""
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
    def test_real_project_passes_phase3_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()


class TestDetection:
    def test_policy001_missing_approval_constraint(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "policy.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["constraints"] = [c for c in schema["constraints"] if "approved_by" not in str(c)]
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "POLICY-001")

    def test_policy002_missing_lifecycle_states(self, project_copy):
        machines_path = project_copy / "architecture" / "state-machines.yaml"
        machines = yaml.safe_load(machines_path.read_text(encoding="utf-8"))
        machines["machines"]["policy_status"]["states"] = ["DRAFT", "ACTIVE", "SUSPENDED", "RETIRED"]
        machines_path.write_text(yaml.safe_dump(machines), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "POLICY-002")

    def test_policy003_self_approval_removed(self, project_copy):
        registry = project_copy / "core" / "policy" / "registry.py"
        text = registry.read_text(encoding="utf-8").replace(
            "policy.created_by == actor.user_id", "False")
        registry.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "POLICY-003")

    def test_risk001_hardcoded_precedence(self, project_copy):
        hazard = project_copy / "core" / "risk" / "hazard.py"
        hazard.write_text(
            'PRECEDENCE = ("EMERGENCY", "CLOSE_ONLY", "BLOCK", "LIMITED", "ALLOW")\n',
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RISK-001")

    def test_risk002_tampered_permission_enum(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "risk_decision.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["enums"]["risk_result"] = ["ALLOW", "BLOCK"]
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RISK-002")

    def test_risk003_missing_critical_fields(self, project_copy):
        config_path = project_copy / "architecture" / "risk-config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        del config["critical_unknown_fields"]
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RISK-003")

    def test_risk005_storage_dependency_in_core_risk(self, project_copy):
        hazard = project_copy / "core" / "risk" / "hazard_store.py"
        hazard.write_text("import sqlite3\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "RISK-005")

    def test_safety001_override_allowed(self, project_copy):
        config_path = project_copy / "architecture" / "risk-config.yaml"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config["hard_limit_override_allowed"] = True
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SAFETY-001")

    def test_decision002_expiration_check_removed(self, project_copy):
        validator = project_copy / "core" / "risk" / "validator.py"
        text = validator.read_text(encoding="utf-8").replace("is_expired", "was_valid")
        validator.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "DECISION-002")

    def test_env003_replay_gate_removed(self, project_copy):
        replay = project_copy / "core" / "risk" / "replay.py"
        text = replay.read_text(encoding="utf-8").replace('"REPLAY"', '"PAPER"')
        replay.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ENV-003")

    def test_audit003_audit_removed(self, project_copy):
        registry = project_copy / "core" / "policy" / "registry.py"
        text = registry.read_text(encoding="utf-8").replace("_audit_policy", "_record_policy")
        registry.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "AUDIT-003")
