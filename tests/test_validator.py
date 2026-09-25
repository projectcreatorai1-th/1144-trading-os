"""Architecture Validator tests (SECTION 20) - detection of every violation class
against deliberately corrupted copies of the project (controlled test data)."""
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


def _load(root: Path, relpath: str) -> dict:
    return yaml.safe_load((root / relpath).read_text(encoding="utf-8"))


def _save(root: Path, relpath: str, data: dict) -> None:
    (root / relpath).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _rules(result, rule_id: str) -> list:
    return [i for i in result.items if i.rule_id == rule_id]


class TestRealProject:
    def test_real_project_passes(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()

    def test_result_is_structured_not_boolean(self):
        result = run_architecture_validation(PROJECT_ROOT)
        summary = result.summary()
        assert set(summary) == {"status", "total_items", "failures", "warnings", "rules_triggered"}
        for item in result.items:
            assert set(vars(item)) == {"rule_id", "severity", "location", "message", "details"}


class TestRegistryViolations:
    def test_duplicate_module_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        arch["modules"].append(dict(arch["modules"][1]))
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-002")
        assert result.status == ValidationStatus.FAIL

    def test_forbidden_dependency_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        risk = next(m for m in arch["modules"] if m["id"] == "core.risk")
        risk["allowed_dependencies"].append("ui.desktop")
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-006")

    def test_unknown_dependency_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        data = next(m for m in arch["modules"] if m["id"] == "core.data")
        data["allowed_dependencies"].append("core.warpdrive")
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-007")

    def test_upward_dependency_without_port_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        cache = next(m for m in arch["modules"] if m["id"] == "platform.cache")
        cache["allowed_dependencies"].append("core.risk")
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-005")

    def test_duplicate_responsibility_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        risk = next(m for m in arch["modules"] if m["id"] == "core.risk")
        other = next(m for m in arch["modules"] if m["id"] == "core.ledger")
        other["responsibility"].append(risk["responsibility"][0])
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SOT-002")

    def test_missing_source_of_truth_detected(self, project_copy):
        (project_copy / "architecture" / "identifiers.yaml").unlink()
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SOT-001")

    def test_contract_without_owner_detected(self, project_copy):
        schema = _load(project_copy, "architecture/schemas/policy.yaml")
        schema["owner"] = "nobody"
        _save(project_copy, "architecture/schemas/policy.yaml", schema)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SOT-003")

    def test_unknown_contract_reference_detected(self, project_copy):
        arch = _load(project_copy, "architecture/architecture.yaml")
        module = next(m for m in arch["modules"] if m["id"] == "core.ledger")
        module["contracts"].append("imaginary_contract")
        _save(project_copy, "architecture/architecture.yaml", arch)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-008")


class TestSchemaViolations:
    def test_duplicate_contract_detected(self, project_copy):
        index = _load(project_copy, "architecture/schema-registry.yaml")
        index["schemas"].append(dict(index["schemas"][0]))
        _save(project_copy, "architecture/schema-registry.yaml", index)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-003")

    def test_missing_contract_version_detected(self, project_copy):
        schema = _load(project_copy, "architecture/schemas/event.yaml")
        del schema["version"]
        _save(project_copy, "architecture/schemas/event.yaml", schema)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ARCH-004")

    def test_breaking_schema_change_detected(self, project_copy):
        schema = _load(project_copy, "architecture/schemas/policy.yaml")
        bumped = "1.9.0"
        schema["version"] = bumped
        schema["history"].append({
            "version": bumped,
            "date": "2026-09-24",
            "summary": "removed a field",
            "changes": [{"kind": "REMOVED_FIELD", "field": "priority"}],
        })
        _save(project_copy, "architecture/schemas/policy.yaml", schema)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SCHEMA-002")
        assert result.status == ValidationStatus.FAIL

    def test_compatibility_violation_detected(self, project_copy):
        schema = _load(project_copy, "architecture/schemas/order.yaml")
        schema["history"].append({
            "version": "2.0.0",
            "date": "2026-09-24",
            "summary": "breaking",
            "changes": [{"kind": "ADDED_REQUIRED_FIELD", "field": "urgent"}],
        })
        _save(project_copy, "architecture/schemas/order.yaml", schema)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SCHEMA-003")


class TestStateMachineViolations:
    def test_unknown_state_detected(self, project_copy):
        machines = _load(project_copy, "architecture/state-machines.yaml")
        machines["machines"]["system_state"]["transitions"].append(
            {"from": "RUNNING", "to": "PARTY"}
        )
        _save(project_copy, "architecture/state-machines.yaml", machines)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SM-002")

    def test_environment_mismatch_detected(self, project_copy):
        manifest = _load(project_copy, "architecture/manifest.yaml")
        manifest["supported_environments"].append("PRODUCTION")
        _save(project_copy, "architecture/manifest.yaml", manifest)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ENV-001")


class TestSourceViolations:
    def test_import_violation_detected(self, project_copy):
        sneaky = project_copy / "core" / "risk" / "sneaky_import.py"
        sneaky.write_text("import ui.desktop\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "IMPORT-001")

    def test_production_marker_detected(self, project_copy):
        marked = project_copy / "platform" / "cache" / "marked.py"
        marked.write_text(
            "value = 1  # to" + "do implement cache later\n", encoding="utf-8"
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "NOPRODUCTION-001")


class TestManifestViolations:
    def test_manifest_module_drift_detected(self, project_copy):
        manifest = _load(project_copy, "architecture/manifest.yaml")
        manifest["modules"].remove("core.ledger")
        _save(project_copy, "architecture/manifest.yaml", manifest)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "MANIFEST-001")

    def test_manifest_version_drift_detected(self, project_copy):
        manifest = _load(project_copy, "architecture/manifest.yaml")
        manifest["contracts"]["identifiers"] = "9.9.9"
        _save(project_copy, "architecture/manifest.yaml", manifest)
        result = run_architecture_validation(project_copy)
        assert _rules(result, "MANIFEST-001")
