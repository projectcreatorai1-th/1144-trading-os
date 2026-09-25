"""No-production-path tests (SECTION 29, RULE 013/014).

Production source must contain no unfinished-work markers, no imitation
implementations, and must never import test-scope code. Ports stay abstract.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from architecture.contracts.registry import find_project_root
from architecture.validator.rules import _WORD_RE

ROOT = find_project_root()
PRODUCTION_AREAS = ("architecture", "core", "platform", "adapters", "ui", "research")


def _production_python_files():
    for area in PRODUCTION_AREAS:
        for py_file in (ROOT / area).rglob("*.py"):
            yield py_file


class TestNoProductionMarkers:
    def test_no_unfinished_markers_in_production(self):
        offenders = [
            str(path.relative_to(ROOT))
            for path in _production_python_files()
            if _WORD_RE.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_test_scope_is_physically_separated(self):
        assert (ROOT / "tests").is_dir()
        assert not (ROOT / "core" / "tests").exists()
        assert not (ROOT / "platform" / "tests").exists()

    def test_production_never_imports_test_scope(self):
        pattern = re.compile(r"^\s*(from|import)\s+tests", re.MULTILINE)
        offenders = [
            str(path.relative_to(ROOT))
            for path in _production_python_files()
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []


class TestPortsStayAbstract:
    @pytest.mark.parametrize("port", [
        "core.events.repository:EventRepository",
        "core.execution.repository:OrderRepository",
        "core.ledger.repository:LedgerRepository",
        "core.portfolio.repository:PositionRepository",
        "platform.audit.repository:AuditRepository",
        "platform.database.contracts:DatabaseAdapter",
        "adapters.broker.contracts:BrokerAdapter",
        "adapters.mt5.contracts:MT5Adapter",
        "adapters.market_data.contracts:MarketDataAdapter",
        "adapters.ai.contracts:ModelRegistry",
    ])
    def test_port_cannot_be_instantiated(self, port):
        import importlib

        module_path, _, class_name = port.partition(":")
        cls = getattr(importlib.import_module(module_path), class_name)
        with pytest.raises(TypeError):
            cls()
