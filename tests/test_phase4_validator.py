"""Phase 4 validator rule tests: detection via corrupted project copies."""
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
    def test_real_project_passes_phase4_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()


class TestDetection:
    def test_strategy001_missing_lifecycle(self, project_copy):
        machines_path = project_copy / "architecture" / "state-machines.yaml"
        machines = yaml.safe_load(machines_path.read_text(encoding="utf-8"))
        machines["machines"]["strategy_lifecycle"]["states"] = ["IDEA", "RETIRED"]
        machines_path.write_text(yaml.safe_dump(machines), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STRATEGY-001")

    def test_strategy002_mutable_strategy(self, project_copy):
        contracts = project_copy / "core" / "strategy" / "contracts.py"
        original = contracts.read_text(encoding="utf-8")
        marker = "@dataclass(frozen=True)\nclass Strategy:"
        assert marker in original
        contracts.write_text(original.replace(marker, "@dataclass\nclass Strategy:", 1),
                             encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STRATEGY-002")

    def test_strategy003_cycle_check_removed(self, project_copy):
        dep = project_copy / "core" / "strategy" / "dependency.py"
        dep.write_text(dep.read_text(encoding="utf-8").replace(
            "DependencyCycleError", "GraphError"), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STRATEGY-003")

    def test_strategy004_capability_provenance_optional(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "strategy_capability.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["provenance"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "STRATEGY-004")

    def test_portfolio002_priority_optional(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "portfolio_membership.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["priority"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "PORTFOLIO-002")

    def test_portfolio005_active_gate_removed(self, project_copy):
        decision = project_copy / "core" / "portfolio" / "decision.py"
        decision.write_text(decision.read_text(encoding="utf-8").replace(
            "is not PortfolioStatus.ACTIVE", "is None"), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "PORTFOLIO-005")

    def test_allocation002_duplicate_budget(self, project_copy):
        (project_copy / "core" / "portfolio" / "budget.py").write_text(
            "from dataclasses import dataclass\n\n@dataclass\nclass RiskBudget:\n    pass\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ALLOCATION-002")

    def test_intent001_not_order_statement_removed(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / "strategy_intent.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["constraints"] = [c for c in schema["constraints"] if "NOT an order" not in str(c)]
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "INTENT-001")

    def test_boundary001_execution_dependency(self, project_copy):
        hazard = project_copy / "core" / "portfolio" / "hazard.py"
        hazard.write_text(
            "from core.execution.contracts import Order\n\ndef x():\n    return Order\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "BOUNDARY-001")

    def test_boundary002_second_risk_engine(self, project_copy):
        gate = project_copy / "core" / "strategy" / "gate.py"
        gate.write_text(
            gate.read_text(encoding="utf-8") + "\n\nclass RiskEngine:\n    pass\n",
            encoding="utf-8",
        )
        result = run_architecture_validation(project_copy)
        assert _rules(result, "BOUNDARY-002")
