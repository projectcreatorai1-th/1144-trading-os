"""Phase 9 validator corruption tests: every GUI rule caught (SECTION 66)."""
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
    shutil.copytree(PROJECT_ROOT, destination,
                    ignore=shutil.ignore_patterns("__pycache__",
                                                  ".pytest_cache", "runtime"))
    return destination


def _rules(result, rule_id: str) -> list:
    return [i for i in result.items if i.rule_id == rule_id]


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    target = root / rel
    src = target.read_text(encoding="utf-8")
    assert old in src, f"anchor missing in {rel}: {old!r}"
    target.write_text(src.replace(old, new), encoding="utf-8")


class TestRealProject:
    def test_real_project_passes_all_phase9_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()

    def test_all_gui_rules_registered(self):
        from architecture.validator.rules import IMPLEMENTED_RULES
        for number in range(1, 13):
            assert f"GUI-{number:03d}" in IMPLEMENTED_RULES


class TestCorruption:
    def test_gui001_forbidden_import(self, project_copy):
        _edit(project_copy, "ui/desktop/viewmodels.py",
              "from architecture.contracts.errors import ContractError",
              "from architecture.contracts.errors import ContractError\n"
              "import core.risk.engine")
        assert _rules(run_architecture_validation(project_copy), "GUI-001")

    def test_gui002_gui_engine(self, project_copy):
        _edit(project_copy, "ui/desktop/viewmodels.py",
              "class WorkstationError(ContractError):",
              "class GuiRiskEngine:\n    pass\n\n\n"
              "class WorkstationError(ContractError):")
        assert _rules(run_architecture_validation(project_copy), "GUI-002")

    def test_gui003_direct_database(self, project_copy):
        _edit(project_copy, "ui/desktop/viewmodels.py",
              "from architecture.contracts.time import ensure_utc, utc_now",
              "from architecture.contracts.time import ensure_utc, utc_now\n"
              "import platform.database")
        assert _rules(run_architecture_validation(project_copy), "GUI-003")

    def test_gui004_direct_broker(self, project_copy):
        _edit(project_copy, "ui/desktop/shell.py",
              "from ui.desktop.viewmodels import",
              "from adapters.mt5.execution import MT5ExecutionAdapter\n"
              "from ui.desktop.viewmodels import")
        assert _rules(run_architecture_validation(project_copy), "GUI-004")

    def test_gui005_direct_oms(self, project_copy):
        _edit(project_copy, "ui/desktop/viewmodels.py",
              "from architecture.contracts.errors import ContractError",
              "from architecture.contracts.errors import ContractError\n"
              "from core.oms.engine import OrderManagementSystem")
        assert _rules(run_architecture_validation(project_copy), "GUI-005")

    def test_gui006_direct_intelligence(self, project_copy):
        _edit(project_copy, "ui/desktop/viewmodels.py",
              "from architecture.contracts.errors import ContractError",
              "from architecture.contracts.errors import ContractError\n"
              "import core.intelligence.contracts")
        assert _rules(run_architecture_validation(project_copy), "GUI-006")

    def test_gui007_gateway_missing(self, project_copy):
        gateway = project_copy / "platform" / "api" / "desktop_gateway.py"
        gateway.unlink()
        assert _rules(run_architecture_validation(project_copy), "GUI-007")

    def test_gui008_region_removed(self, project_copy):
        _edit(project_copy, "ui/desktop/shell.py",
              'text="C - INSPECTOR"', 'text="X - PANEL"')
        assert _rules(run_architecture_validation(project_copy), "GUI-008")

    def test_gui009_unknown_state_removed(self, project_copy):
        # remove EVERY UNKNOWN declaration occurrence in contracts
        target = project_copy / "ui" / "desktop" / "contracts.py"
        src = target.read_text(encoding="utf-8")
        target.write_text(src.replace("UNKNOWN", "UNKNWN"),
                          encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "GUI-009")

    def test_gui010_env_label_removed(self, project_copy):
        target = project_copy / "ui" / "desktop" / "shell.py"
        src = target.read_text(encoding="utf-8")
        target.write_text(src.replace("ENV:", "X:"), encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "GUI-010")

    def test_gui011_confirmation_removed(self, project_copy):
        target = project_copy / "ui" / "desktop" / "viewmodels.py"
        src = target.read_text(encoding="utf-8")
        target.write_text(src.replace("confirmation required",
                                      "confirmation needed"),
                          encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "GUI-011")

    def test_gui012_recovery_removed(self, project_copy):
        target = project_copy / "ui" / "desktop" / "viewmodels.py"
        src = target.read_text(encoding="utf-8")
        assert "default_workspace()" in src
        target.write_text(src.replace("default_workspace()", "None"),
                          encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "GUI-012")


class TestPhaseBoundary:
    def test_boundx_web_workspace_blocked(self, project_copy):
        app = project_copy / "ui" / "web" / "workspace.py"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text("def main():\n    return 1\n", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")

    def test_boundx_mobile_blocked(self, project_copy):
        mobile = project_copy / "ui" / "mobile" / "app.py"
        mobile.parent.mkdir(parents=True, exist_ok=True)
        mobile.write_text("def main():\n    return 1\n", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")

    def test_desktop_app_now_legitimate(self, project_copy):
        """ui/desktop/app.py is Phase 9 scope - it must NOT trip BOUNDX."""
        result = run_architecture_validation(project_copy)
        assert not [i for i in result.items
                    if i.rule_id == "BOUNDX-001"
                    and "desktop" in i.location]
