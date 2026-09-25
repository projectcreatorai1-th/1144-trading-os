"""Phase 5 validator rule tests: detection via corrupted project copies."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

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
    def test_real_project_passes_phase5_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()


class TestDetection:
    def test_oms002_oms_depends_on_mt5(self, project_copy):
        oms = project_copy / "core" / "oms" / "engine.py"
        oms.write_text(oms.read_text(encoding="utf-8")
                       + "\nfrom adapters.mt5.execution import MT5ExecutionAdapter\n",
                       encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "OMS-002")

    def test_ems002_ems_grants_risk(self, project_copy):
        ems = project_copy / "core" / "ems" / "engine.py"
        ems.write_text(ems.read_text(encoding="utf-8")
                       + "\nGRANT = RiskResult.ALLOW\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EMS-002")

    def test_exec001_boundary_removed(self, project_copy):
        boundary = project_copy / "core" / "execution" / "boundary.py"
        boundary.unlink()
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EXEC-001")

    def test_exec003_unknown_removed(self, project_copy):
        boundary = project_copy / "core" / "execution" / "boundary.py"
        text = boundary.read_text(encoding="utf-8").replace(
            "ExecutionDecision.UNKNOWN", "ExecutionDecision.NONE")
        boundary.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EXEC-003")

    def test_exec005_second_risk_engine(self, project_copy):
        ems = project_copy / "core" / "ems" / "engine.py"
        ems.write_text(ems.read_text(encoding="utf-8")
                       + "\n\nclass RiskEngine:\n    pass\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "EXEC-005")

    def test_mt5_001_mt5_leaks_into_domain(self, project_copy):
        hazard = project_copy / "core" / "oms" / "mt5_leak.py"
        hazard.write_text("from adapters.mt5.transport import MetaTrader5Transport\n",
                          encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "MT5-001")

    def test_envx_001_fallback_added(self, project_copy):
        ems = project_copy / "core" / "ems" / "engine.py"
        text = ems.read_text(encoding="utf-8").replace(
            "No execution adapter registered", "Adapter missing")
        ems.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "ENVX-001")

    def test_posx_001_state_engine_removed(self, project_copy):
        proj = project_copy / "core" / "oms" / "projection.py"
        text = proj.read_text(encoding="utf-8").replace(
            "StateCategory.POSITION_STATE", "StateCategory.SYSTEM_STATE")
        proj.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "POSX-001")

    def test_ledx_001_ledger_bypassed(self, project_copy):
        proj = project_copy / "core" / "oms" / "projection.py"
        text = proj.read_text(encoding="utf-8").replace("LedgerPostingService", "Posting")
        proj.write_text(text, encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "LEDX-001")

    def test_secx_001_credential_in_source(self, project_copy):
        hazard = project_copy / "adapters" / "mt5" / "creds.py"
        hazard.write_text('password = "hunter2"\n', encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SECX-001")

    def test_aix_001_ai_execution(self, project_copy):
        ai = project_copy / "adapters" / "ai" / "contracts.py"
        ai.write_text(ai.read_text(encoding="utf-8")
                      + "\n\ndef submit_order(x):\n    return x\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "AIX-001")

    def test_boundx_001_phase6_code(self, project_copy):
        # Phase-boundary rule (updated for Phase 6): AI/ML authority modules
        # are next-phase scope and must be rejected.
        (project_copy / "core" / "ml").mkdir(parents=True, exist_ok=True)
        (project_copy / "core" / "ml" / "authority.py").write_text(
            "def auto_trade():\n    return 1\n", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "BOUNDX-001")
