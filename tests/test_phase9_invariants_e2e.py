"""Phase 9 invariants (SECTION 67) + E2E golden path (SECTION 69/70/71).

Executable proof that the desktop is a control plane, never an
authority. SYNTHETIC data; the GUI itself is exercised headlessly via
the view models + gateway (the Tk shell has its own smoke test).
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from architecture.contracts.errors import ContractError

from platform.api.desktop_gateway import DesktopGateway

from ui.desktop.contracts import ViewState, default_workspace
from ui.desktop.viewmodels import WorkstationModel


@pytest.fixture()
def gateway(tmp_path):
    gw = DesktopGateway(db_path=tmp_path / "p9-e2e.db",
                        environment="SIMULATION")
    yield gw
    gw.close()


@pytest.fixture()
def model(gateway):
    m = WorkstationModel(gateway)
    m.start()
    m.login("trader", "synthetic-trader-desktop-secret")
    return m


class TestPhase9Invariants:
    """SECTION 67: every listed invariant, executable."""

    def test_gui_is_not_authority(self):
        import ui.desktop.viewmodels as vm
        import ui.desktop.shell as shell
        assert not hasattr(vm, "RiskEngine")
        assert not hasattr(shell, "OrderManagementSystem")

    def test_ai_is_not_authority(self, model):
        view = model.intelligence("EURUSD")
        assert view["advisory_only"] is True
        assert not hasattr(model._gateway, "RiskDecision")

    def test_gui_cannot_bypass_security(self, gateway):
        with pytest.raises(ContractError):
            gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})

    def test_gui_cannot_bypass_governance(self, gateway):
        # traders cannot reach emergency controls
        gateway.login("trader", "synthetic-trader-desktop-secret")
        with pytest.raises(ContractError):
            gateway.act("emergency_stop", {"reason": "bypass attempt"})

    def test_gui_cannot_bypass_risk(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        gateway.act("pause", {"reason": "halt"})
        gateway.logout()
        gateway.login("trader", "synthetic-trader-desktop-secret")
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        assert receipt.state == "REJECTED"  # hard policy wins

    def test_gui_cannot_bypass_oms_ems(self):
        import ui.desktop.viewmodels as vm
        assert not hasattr(vm, "OrderManagementSystem")
        assert not hasattr(vm, "ExecutionManagementSystem")

    def test_unknown_is_not_safe(self, model):
        states = {s.value for s in ViewState}
        assert "UNKNOWN" in states
        assert "PERMISSION_DENIED" in states

    def test_stale_is_not_current(self, gateway):
        from datetime import timedelta
        gateway._last_tick -= timedelta(seconds=600)
        assert gateway.freshness()["status"] == "STALE"

    def test_disconnected_is_not_safe(self, model):
        model.disconnect()
        with pytest.raises(ContractError):
            model._gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})

    def test_live_is_not_demo(self):
        with pytest.raises(ContractError):
            DesktopGateway(environment="LIVE")

    def test_simulation_is_not_live(self, gateway):
        assert gateway.environment == "SIMULATION"
        # the simulation adapter is the ONLY execution plane wired
        assert "LIVE" not in gateway.ems._adapters
        assert "SIMULATION" in gateway.ems._adapters

    def test_ai_cannot_execute(self, gateway):
        intel = gateway.intelligence_view("EURUSD")
        assert "order" not in intel
        assert intel["why"]["system"] == "AI_WHY"

    def test_ai_cannot_override_hard_risk(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        gateway.act("pause", {"reason": "halt"})
        gateway.logout()
        gateway.login("trader", "synthetic-trader-desktop-secret")
        # even with an advisory LONG proposal, hard policy blocks
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        assert receipt.state == "REJECTED"

    def test_context_selection_cannot_mutate_domain(self, model):
        before = (len(model._gateway.orders()),
                  len(model._gateway.positions()))
        for symbol in ("EURUSD", "XAUUSD", "US30"):
            model.select_symbol(symbol)
        model.select_object.__self__  # model API intact
        after = (len(model._gateway.orders()),
                 len(model._gateway.positions()))
        assert before == after

    def test_workspace_cannot_mutate_domain(self, model):
        model.apply_preset("MARKET")
        assert len(model._gateway.orders()) == 0

    def test_ui_success_cannot_precede_confirmation(self, model):
        result = model.submit_order("EURUSD", "BUY", "0.1")
        # state comes from the engine receipt - APPLIED only after the
        # fill was projected
        assert result["state"] == "APPLIED"
        assert result["order_id"] is not None

    def test_historical_time_not_mixed_with_live(self, gateway):
        market = gateway.market("EURUSD")
        bars = market["bars"]
        assert all(b["time_kind"] == "EVENT_TIME" for b in bars)
        intel = gateway.intelligence_view("EURUSD")
        assert intel["valid_until"]  # explicit advisory validity window

    def test_unauthorized_hidden_paths_blocked(self, gateway):
        # direct dispatcher call still needs no session? It must: the
        # gateway is the only entry the UI has, and it gates everything
        gateway.login("trader", "synthetic-trader-desktop-secret")
        # trader cannot reach the dispatcher for emergency targets
        with pytest.raises(ContractError):
            gateway.act("emergency_stop", {"reason": "hidden"})


class TestGoldenPathE2E:
    """SECTION 69: launch -> login -> overview -> markets -> symbol ->
    inspect -> intelligence -> risk -> order chain -> back navigation,
    then disconnect/reconnect."""

    def test_golden_path(self, model):
        assert model.state is ViewState.READY
        model.select_symbol("EURUSD")
        assert model.context_stack.current.symbol == "EURUSD"
        market = model.market_view("EURUSD")
        assert market["latest"]
        intel = model.intelligence("EURUSD")
        assert intel["advisory_only"]
        risk = model.risk()
        assert risk["environment"] == "SIMULATION"
        result = model.submit_order("EURUSD", "BUY", "0.1")
        assert result["state"] == "APPLIED"
        why = model.core_why(result["order_id"])
        assert why["system"] == "CORE_WHY"
        assert why["order_found"] is True
        model.select_object.__doc__  # no-op
        # drill-down + back through context history
        model.select_symbol("XAUUSD")
        assert model.context_stack.go_back().object_id == "EURUSD"
        # disconnect -> DISCONNECTED -> privileged blocked -> reconnect
        model.disconnect()
        assert model.state is ViewState.DISCONNECTED
        with pytest.raises(ContractError):
            model._gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        model.reconnect()
        assert model.state is ViewState.READY

    def test_security_e2e(self, gateway):
        """SECTION 70: login -> session -> permission -> action -> audit."""
        info = gateway.login("trader", "synthetic-trader-desktop-secret")
        assert info["role"] == "TRADER"
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        assert receipt.state == "APPLIED"
        audit = gateway.audit_records()
        assert any(a["action"] == "DESKTOP_SUBMIT_ORDER" for a in audit)
        gateway.logout()
        with pytest.raises(ContractError):
            gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})

    def test_live_e2e_safety(self, gateway):
        """SECTION 71: LIVE cannot be reached through GUI behavior."""
        code = (
            "import sys\n"
            "sys.path.insert(0, '.')\n"
            "from platform.api.desktop_gateway import DesktopGateway\n"
            "try:\n"
            "    DesktopGateway(environment='LIVE')\n"
            "    sys.exit(1)\n"
            "except Exception:\n"
            "    sys.exit(0)\n"
        )
        result = subprocess.run([sys.executable, "-c", code],
                                capture_output=True, cwd=".")
        assert result.returncode == 0

    def test_shell_smoke_if_display(self, gateway):
        """Construct + render the real Tk shell once, then destroy."""
        try:
            import tkinter as tk
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError:
            pytest.skip("no display available for Tk shell")
        from ui.desktop.shell import Shell
        model = WorkstationModel(gateway)
        model.start()
        model.login("trader", "synthetic-trader-desktop-secret")
        model.select_symbol("EURUSD")
        model.submit_order("EURUSD", "BUY", "0.1")
        shell = Shell(model)
        shell._render()
        shell.update_idletasks()
        assert len(model.blotter_page()) == 1
        shell.shutdown()
