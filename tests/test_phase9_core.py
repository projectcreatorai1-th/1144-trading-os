"""Phase 9 unit tests: gateway, view models, contracts (headless).

The workstation is a control plane: these tests prove the real chain
behaves through the gateway and that UI behavior never mutates domain
state directly. SYNTHETIC market data only.
"""
from __future__ import annotations

import pytest

from architecture.contracts.errors import ContractError

from platform.api.desktop_gateway import DesktopGateway

from ui.desktop.contracts import (
    PanelDockState,
    PanelState,
    UIContext,
    ViewContextKind,
    ViewState,
    WorkspaceState,
    default_workspace,
)
from ui.desktop.viewmodels import (
    BlotterFilter,
    ContextStack,
    VirtualWindow,
    WorkstationModel,
)


@pytest.fixture()
def gateway(tmp_path):
    gw = DesktopGateway(db_path=tmp_path / "desktop.db",
                        environment="SIMULATION")
    yield gw
    gw.close()


@pytest.fixture()
def model(gateway):
    m = WorkstationModel(gateway)
    m.start()
    m.login("trader", "synthetic-trader-desktop-secret")
    return m


# --------------------------------------------------------------------- #
# Gateway                                                               #
# --------------------------------------------------------------------- #
class TestGateway:
    def test_login_persona_and_role(self, gateway):
        info = gateway.login("trader", "synthetic-trader-desktop-secret")
        assert info["role"] == "TRADER"

    def test_login_wrong_secret_fails(self, gateway):
        with pytest.raises(ContractError):
            gateway.login("trader", "wrong-secret-value")

    def test_unknown_persona_fails(self, gateway):
        with pytest.raises(ContractError):
            gateway.login("admin", "whatever-secret")

    def test_submit_order_full_chain(self, gateway):
        gateway.login("trader", "synthetic-trader-desktop-secret")
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        assert receipt.state == "APPLIED"
        assert receipt.order_id and receipt.position_id
        assert len(gateway.orders()) == 1
        assert len(gateway.positions()) == 1
        assert len(gateway.ledger_entries()) >= 1

    def test_pause_blocks_new_orders(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        gateway.act("pause", {"reason": "operator"})
        gateway.logout()
        gateway.login("trader", "synthetic-trader-desktop-secret")
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        assert receipt.state == "REJECTED"
        assert "EMERGENCY" in receipt.reasons[0] or \
            "BLOCK" in receipt.reasons[0]

    def test_trader_cannot_emergency_stop(self, gateway):
        gateway.login("trader", "synthetic-trader-desktop-secret")
        with pytest.raises(ContractError):
            gateway.act("emergency_stop", {"reason": "x"})

    def test_risk_manager_can_pause(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        receipt = gateway.act("pause", {"reason": "risk desk"})
        assert receipt.state == "APPLIED"
        assert gateway.risk_state.current() == "PAUSE"

    def test_unauthenticated_act_blocked(self, gateway):
        with pytest.raises(ContractError):
            gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})

    def test_rate_limited_after_burst(self, gateway):
        gateway.login("risk_manager", "synthetic-risk-desktop-secret")
        # pause is idempotent at the state machine (no-op once PAUSED),
        # so the burst exercises the RATE LIMIT, not the machine
        for _ in range(30):
            gateway.act("pause", {"reason": "burst"})
        with pytest.raises(ContractError):
            gateway.act("pause", {"reason": "over limit"})

    def test_freshness_and_disconnect(self, gateway):
        assert gateway.freshness()["status"] == "CURRENT"
        gateway.disconnect()
        assert gateway.freshness()["status"] == "DISCONNECTED"
        with pytest.raises(ContractError):
            gateway.act("submit_order", {
                "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        result = gateway.reconnect()
        assert result["resynchronized"] is True

    def test_subscription_dedup(self, gateway):
        from core.events.contracts import EventType, build_event
        from architecture.contracts.identifiers import new_identifier
        seen = []
        token = gateway.subscribe(seen.append)
        event = build_event(
            event_type=EventType.SYSTEM_STATE_CHANGED,
            source="test", source_id="t", environment="SIMULATION",
            correlation_id=new_identifier("correlation_id"),
            event_time=_now(), received_time=_now(),
            payload={"x": 1}, event_id=new_identifier("event_id"))
        gateway._publish(event)
        gateway._publish(event)  # duplicate event_id
        assert len(seen) == 1
        gateway.unsubscribe(token)
        with pytest.raises(ContractError):
            gateway.unsubscribe(token)

    def test_intelligence_is_advisory(self, gateway):
        view = gateway.intelligence_view("EURUSD")
        assert view["advisory_only"] is True
        assert view["why"]["system"] == "AI_WHY"
        assert "why" in view

    def test_core_why_is_separate(self, gateway):
        gateway.login("trader", "synthetic-trader-desktop-secret")
        receipt = gateway.act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})
        why = gateway.core_why(receipt.order_id)
        assert why["system"] == "CORE_WHY"
        assert why["order_found"] is True

    def test_search_finds_real_objects(self, gateway):
        gateway.login("trader", "synthetic-trader-desktop-secret")
        receipt = gateway.act("submit_order", {
            "symbol": "XAUUSD", "side": "BUY", "quantity": "0.1"})
        results = gateway.search(receipt.order_id[:14])
        assert any(r["kind"] == "ORDER" for r in results)
        assert any(r["kind"] == "SYMBOL" for r in gateway.search("XAU"))


def _risk_event(gateway):
    from core.events.contracts import EventType, build_event
    from architecture.contracts.identifiers import new_identifier
    return build_event(
        event_type=EventType.RISK_STATE_CHANGED, source="test",
        source_id="risk-engine", environment=gateway.environment,
        correlation_id=new_identifier("correlation_id"),
        event_time=_now(), received_time=_now(), payload={},
        event_id=new_identifier("event_id"))


def _now():
    from architecture.contracts.time import utc_now
    return utc_now()


# --------------------------------------------------------------------- #
# View models                                                           #
# --------------------------------------------------------------------- #
class TestViewModel:
    def test_context_stack_back_forward(self, model):
        first = model.select_symbol("EURUSD")
        second = model.select_symbol("XAUUSD")
        assert model.context_stack.current is second
        assert model.context_stack.go_back() is first
        assert model.context_stack.go_forward() is second

    def test_context_selection_never_mutates_domain(self, model):
        before = len(model._gateway.orders())
        model.select_symbol("EURUSD")
        model.select_object(ViewContextKind.STRATEGY, "str_x")
        assert len(model._gateway.orders()) == before

    def test_blotter_filter_global_vs_local(self, model):
        model.select_symbol("EURUSD")
        assert model.blotter_filter.effective_symbol == "EURUSD"
        model.blotter_filter.set_local("XAUUSD")
        assert model.blotter_filter.effective_symbol == "XAUUSD"
        model.select_symbol("US30")  # global change must not stomp local
        assert model.blotter_filter.effective_symbol == "XAUUSD"
        model.blotter_filter.clear_local()
        assert model.blotter_filter.effective_symbol == "US30"

    def test_virtual_window_pagination(self):
        window = VirtualWindow(page_size=10)
        rows = list(range(35))
        assert window.view(rows) == list(range(10))
        window.next(rows)
        assert window.view(rows) == list(range(10, 20))
        window.next(rows)
        window.next(rows)
        assert window.view(rows) == list(range(30, 35))  # bounded tail

    def test_presets_and_panels(self, model):
        for preset in ("OVERVIEW", "MARKET", "EXECUTION", "RESEARCH",
                       "INTELLIGENCE", "RISK", "OPERATIONS"):
            workspace = model.apply_preset(preset)
            assert workspace.preset == preset
            regions = {p.region for p in workspace.panels}
            assert regions == {"B", "C", "D", "E", "F", "G"}

    def test_panel_docking(self, model):
        model.apply_preset("MARKET")
        model.dock_panel("chart", PanelDockState.FLOATING)
        chart = next(p for p in model.workspace.panels
                     if p.panel_id == "chart")
        assert chart.dock is PanelDockState.FLOATING

    def test_workspace_persistence_roundtrip_and_recovery(self, model):
        model.apply_preset("RISK")
        persisted = model.persist_workspace()
        restored = model.restore_workspace(persisted)
        assert restored.preset == "RISK"
        corrupted = {"preset": "NOT_A_PRESET", "panels": [
            {"panel_id": "x", "region": "Z"}]}
        recovered = model.restore_workspace(corrupted)
        assert recovered == default_workspace()

    def test_action_states_lifecycle(self, model):
        result = model.submit_order("EURUSD", "BUY", "0.1")
        assert result["state"] in ("APPLIED", "REJECTED", "FAILED",
                                   "UNKNOWN", "PENDING")

    def test_dangerous_command_needs_confirmation(self, model):
        result = model.run_command("emergency_stop", confirmed=False)
        assert result["state"] == "REJECTED"

    def test_unknown_command_rejected(self, model):
        result = model.run_command("nope", confirmed=True)
        assert result["state"] == "REJECTED"

    def test_palette_pause_as_risk_manager(self, gateway):
        model = WorkstationModel(gateway)
        model.start()
        model.login("risk_manager", "synthetic-risk-desktop-secret")
        result = model.run_command("pause", confirmed=True)
        assert result["state"] == "APPLIED"

    def test_palette_pause_blocked_for_trader(self, model):
        result = model.run_command("pause", confirmed=True)
        assert result["state"] in ("REJECTED", "UNKNOWN", "FAILED")

    def test_notifications_bounded_and_unread(self, model):
        for i in range(150):
            model.push_notification(category="TEST", title=f"n{i}")
        assert len(model.notifications) == 100
        assert model.unread_count() == 100
        model.mark_all_read()
        assert model.unread_count() == 0

    def test_search_opens_context(self, model):
        model.submit_order("EURUSD", "BUY", "0.1")
        results = model.search("EUR")
        assert results
        context = model.open_search_result(results[0])
        assert context.environment == "SIMULATION"

    def test_disconnect_reconnect_states(self, model):
        model.disconnect()
        assert model.state is ViewState.DISCONNECTED
        model.reconnect()
        assert model.state is ViewState.READY


# --------------------------------------------------------------------- #
# Contracts                                                             #
# --------------------------------------------------------------------- #
class TestContracts:
    def test_bad_region_rejected(self):
        from architecture.contracts.errors import ContractValidationError
        panel = PanelState(panel_id="x", region="Z")
        with pytest.raises(ContractValidationError):
            panel.validate()

    def test_context_requires_environment(self):
        from architecture.contracts.errors import ContractValidationError
        with pytest.raises(ContractValidationError):
            UIContext(kind=ViewContextKind.ORDER, object_id="o",
                      environment="").validate()

    def test_default_workspace_valid(self):
        workspace = default_workspace("OVERVIEW")
        workspace.validate()
        assert {p.region for p in workspace.panels} == \
               {"A", "B", "C", "D", "E", "F", "G"}
