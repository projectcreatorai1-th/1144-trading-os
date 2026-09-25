"""Phase 16 - UI semantic contract registry coverage."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from architecture.contracts.errors import ContractError

UI_CONTRACT = Path(__file__).resolve().parents[1] / "ui" / "desktop" \
    / "ui_contract.yaml"

REQUIRED_FIELDS = ("control_id", "accessible_name", "role", "action",
                   "expected_event", "expected_state_change", "covered_by")

REQUIRED_WORKFLOWS = {"Login", "Navigation", "Search", "Order", "Pause",
                      "Close Only", "Emergency", "Command Palette",
                      "Inspector", "Blotter", "Research", "Risk",
                      "Execution"}


def _contract() -> dict:
    return yaml.safe_load(UI_CONTRACT.read_text(encoding="utf-8"))


def _collected_test_ids() -> set[str]:
    """Class names present in the GUI repair suite (verbose collect tree;
    this project's -q collect format only prints per-file counts)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only",
         "tests/test_phase9_gui_repair.py"],
        capture_output=True, text=True, cwd=str(UI_CONTRACT.parents[2]))
    ids = {line.strip() for line in proc.stdout.splitlines()
           if line.strip().startswith("tests/") and "::" in line}
    classes = {node.rsplit("::", 1)[0]
               for node in ids if node.count("::") >= 2}
    return classes


class TestUiContract:
    def test_every_control_declares_full_contract(self):
        data = _contract()
        ids = []
        for control in data["controls"]:
            for field in REQUIRED_FIELDS:
                assert control.get(field), \
                    f"{control.get('control_id')} missing {field}"
            ids.append(control["control_id"])
        assert len(ids) == len(set(ids)), "duplicate control_id"

    def test_all_critical_workflows_covered_with_real_controls(self):
        data = _contract()
        control_ids = {c["control_id"] for c in data["controls"]}
        workflows = {w["workflow"]: w["controls"]
                     for w in data["critical_workflows"]}
        for workflow in REQUIRED_WORKFLOWS:
            assert workflow in workflows, f"workflow {workflow} missing"
            assert workflows[workflow], f"{workflow} has no controls"
            for control in workflows[workflow]:
                assert control in control_ids, \
                    f"{workflow} references unknown control {control}"

    def test_coverage_points_at_real_collected_tests(self):
        data = _contract()
        collected = _collected_test_ids()
        assert collected, "collection produced no test ids"
        classes = collected
        for control in data["controls"]:
            covered = control["covered_by"]
            assert covered in classes, \
                f"{control['control_id']} covered_by unknown suite " \
                f"{covered}"

    def test_environment_findings_documented_not_hidden(self):
        data = _contract()
        findings = {f["id"]: f for f in data["environment_findings"]}
        layout = findings["keyboard_layout_non_latin"]
        assert layout["status"].startswith("documented")
        assert "KEYBOARD_LAYOUT_FINDING" in layout["fact"]

    def test_rejection_visibility_invariant_in_contract(self):
        # INV-GUI-002: rejected action has visible reason - the four
        # protective controls must declare banner state changes
        data = _contract()
        protective = {c["control_id"]: c for c in data["controls"]
                      if c["control_id"].startswith("action_")}
        assert len(protective) == 4
        for control in protective.values():
            assert "banner" in control["expected_state_change"]
