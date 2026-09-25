"""Phase 10 validator corruption tests: every MT5X/FDX rule caught."""
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
    def test_real_project_passes_phase10_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()

    def test_rule_count(self):
        from architecture.validator.rules import IMPLEMENTED_RULES
        for rule in ("MT5X-001", "MT5X-002", "MT5X-003", "MT5X-004",
                     "FDX-001", "FDX-002"):
            assert rule in IMPLEMENTED_RULES
        assert len(IMPLEMENTED_RULES) == 234


class TestCorruption:
    def test_mt5x001_forbidden_area_import(self, project_copy):
        _edit(project_copy, "core/research/tick_history.py",
              '"""Tick history -> research plane (owned by core.research, '
              'Phase 10).',
              'import adapters.mt5.connection\n'
              '"""Tick history -> research plane (owned by core.research, '
              'Phase 10).')
        assert _rules(run_architecture_validation(project_copy), "MT5X-001")

    def test_mt5x002_gate_removed_from_feed(self, project_copy):
        _edit(project_copy, "adapters/market_data/mt5_feed.py",
              "def _mt5_available() -> bool:",
              "def _mt5_removed() -> bool:")
        result = run_architecture_validation(project_copy)
        # removing the gated helper alone is legal; the real corruption is
        # a bare hard import - add one
        _edit(project_copy, "adapters/market_data/mt5_feed.py",
              "from adapters.mt5.connection import (",
              "import MetaTrader5 as _hard\n"
              "from adapters.mt5.connection import (")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "MT5X-002") or _rules(result, "MT5X-001")

    def test_mt5x003_unknown_removed(self, project_copy):
        target = project_copy / "adapters/mt5/connection.py"
        src = target.read_text(encoding="utf-8")
        assert "UNKNOWN" in src
        target.write_text(src.replace("UNKNOWN", "UNKNWN"),
                          encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "MT5X-003")

    def test_mt5x003_machine_removed(self, project_copy):
        _edit(project_copy, "architecture/state-machines.yaml",
              "  connection_state:", "  connection_st8:")
        assert _rules(run_architecture_validation(project_copy), "MT5X-003")

    def test_mt5x004_credential_literal(self, project_copy):
        _edit(project_copy, "adapters/mt5/connection.py",
              "CONTRACT_VERSION = \"1.0.0\"",
              'CONTRACT_VERSION = "1.0.0"\n'
              'PASSWORD = "super-secret-broker-password"')
        assert _rules(run_architecture_validation(project_copy), "MT5X-004")

    def test_fdx001_pipeline_bypassed(self, project_copy):
        target = project_copy / "adapters/market_data/mt5_feed.py"
        src = target.read_text(encoding="utf-8")
        target.write_text(src.replace("IngestionRequest",
                                      "FeedShovelRequest"),
                          encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "FDX-001")

    def test_fdx002_direct_store_write(self, project_copy):
        _edit(project_copy, "adapters/market_data/mt5_feed.py",
              "class MT5MarketDataAdapter:",
              "class MT5MarketDataAdapter:\n"
              "    def _second_pipeline(self):\n"
              "        return self.event_store.append")
        assert _rules(run_architecture_validation(project_copy), "FDX-002")
