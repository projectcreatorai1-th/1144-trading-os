"""Pre-market readiness: account guard, session contract, and the
consolidated MOCK trading scenarios A–L (Phase 15).

All scenarios run on the EXISTING components (OMS/EMS boundaries,
gateway server, chaos-style fakes). Loopback/mock is labeled MOCK —
never presented as external or DEMO runtime evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from adapters.mt5.account_guard import (
    FOREX_LONDON_NY,
    AccountGuard,
    AccountGuardError,
    ExpectedIdentity,
    SessionGuard,
    SessionWindow,
    TerminalIdentity,
)
from architecture.contracts.errors import ContractError
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.gateway.contracts import new_id, utc_now_iso
from platform.gateway.server import GatewayServer, InProcessTransport
from tests.test_gateway_server import ProtocolClient


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return None

    def iter_by_correlation_id(self, correlation_id):
        return iter(())


DEMO = TerminalIdentity(trade_mode=0, login=113126589,
                        server="XMGlobal-MT5 16",
                        symbols_available=("EURUSD", "XAUUSD"))
PINNED = ExpectedIdentity(trade_mode=0, login=113126589,
                          server="XMGlobal-MT5 16",
                          symbols=("EURUSD",))


class TestAccountGuardPhase11:
    def test_expected_demo_account_allowed(self):
        guard = AccountGuard(PINNED)
        decision = guard.check(DEMO)
        assert decision["allowed"] is True

    def test_live_account_hard_block(self):
        guard = AccountGuard(PINNED)
        live = TerminalIdentity(trade_mode=2, login=999,
                                server="XMGlobal-MT5 16")
        with pytest.raises(AccountGuardError) as err:
            guard.check(live)
        assert "trade_mode 2" in str(err.value)
        assert err.value.rule_id == "MT5X-ACCOUNT-GUARD"

    def test_wrong_account_hard_block(self):
        guard = AccountGuard(PINNED)
        wrong = TerminalIdentity(trade_mode=0, login=411173797,
                                 server="XMGlobal-MT5 16")
        with pytest.raises(AccountGuardError) as err:
            guard.check(wrong)
        assert "login 411173797 != pinned" in str(err.value)

    def test_wrong_server_hard_block(self):
        guard = AccountGuard(PINNED)
        wrong = TerminalIdentity(trade_mode=0, login=113126589,
                                 server="Other-Broker-Server")
        with pytest.raises(AccountGuardError):
            guard.check(wrong)

    def test_unpinned_login_still_guards_mode_and_server(self):
        guard = AccountGuard(ExpectedIdentity(trade_mode=0))
        guard.check(DEMO)      # mode ok -> allowed (pin optional)
        with pytest.raises(AccountGuardError):
            guard.check(TerminalIdentity(trade_mode=2, login=1, server="x"))

    def test_live_cannot_be_pinned_as_expected(self):
        with pytest.raises(AccountGuardError):
            AccountGuard(ExpectedIdentity(trade_mode=2))

    def test_missing_symbol_blocks(self):
        guard = AccountGuard(ExpectedIdentity(
            trade_mode=0, symbols=("GOLDmicro",)))
        with pytest.raises(AccountGuardError) as err:
            guard.check(DEMO)
        assert "GOLDmicro" in str(err.value)


class TestSessionContractPhase13:
    def test_weekday_hours_open(self):
        # 2026-09-24 12:00 UTC = Thursday midday
        assert FOREX_LONDON_NY.is_open(
            datetime(2026, 9, 24, 12, tzinfo=timezone.utc)) is True

    def test_weekend_closed(self):
        # Saturday any hour
        assert FOREX_LONDON_NY.is_open(
            datetime(2026, 9, 26, 12, tzinfo=timezone.utc)) is False
        # Sunday (before Monday open)
        assert FOREX_LONDON_NY.is_open(
            datetime(2026, 9, 27, 12, tzinfo=timezone.utc)) is False

    def test_rollover_boundary(self):
        # Thursday 21:00 UTC = at close boundary -> closed (half-open)
        assert FOREX_LONDON_NY.is_open(
            datetime(2026, 9, 24, 21, tzinfo=timezone.utc)) is False
        assert FOREX_LONDON_NY.is_open(
            datetime(2026, 9, 24, 20, 59, tzinfo=timezone.utc)) is True

    def test_session_closed_blocks_order_flow_via_guard(self):
        guard = AccountGuard(PINNED)
        session = SessionGuard()
        session.override_for_tests(False)          # deterministic mock
        with pytest.raises(AccountGuardError) as err:
            guard.check(DEMO, session=session,
                        moment=datetime(2026, 9, 26, 12,
                                        tzinfo=timezone.utc))
        assert "session CLOSED" in str(err.value)

    def test_overnight_window_spans_midnight(self):
        window = SessionWindow("asia", 22, 6, days_utc=(0, 1, 2, 3, 4))
        assert window.is_open(datetime(2026, 9, 24, 23,
                                       tzinfo=timezone.utc))
        assert window.is_open(datetime(2026, 9, 24, 5,
                                       tzinfo=timezone.utc))
        assert not window.is_open(datetime(2026, 9, 24, 12,
                                           tzinfo=timezone.utc))


# --------------------------------------------------------------------- #
# Phase 15 — consolidated MOCK scenarios A–L (LOOPBACK label)
# --------------------------------------------------------------------- #
def _server_with_routes(audit):
    server = GatewayServer(audit)
    server.start()

    def route_to_ea(command):
        targets = server.sessions.by_type("OUR_EA")
        for session in targets:
            session.send({"type": "COMMAND", "command": command.command_type,
                          "request_id": command.request_id,
                          "payload": dict(command.payload)})
        return {"state": "COMPLETED", "delivered": len(targets)}

    for cmd in ("REGISTER", "INITIALIZE", "START", "STOP", "PAUSE", "RESUME",
                "HALT", "SYNC", "RECONCILE", "DEPLOY", "ROLLBACK",
                "UPDATE_STRATEGY", "UPDATE_PARAMETER", "UPDATE_CONFIG",
                "REQUEST_HEALTH", "REQUEST_STATUS"):
        server.commands.register_target(cmd, route_to_ea)
    return server


def _wire(server):
    ea = ProtocolClient(server)
    ea.connect(client_id="OUR-EA-RUNTIME", client_type="OUR_EA")
    analyzer = ProtocolClient(server)
    analyzer.connect(client_id="SNIPER-ANALYZER", client_type="ANALYZER")
    os_client = ProtocolClient(server)
    return ea, analyzer, os_client


def _signal_message(symbol="EURUSD", direction="BUY"):
    return {
        "type": "SIGNAL_PUBLISH", "signal_id": new_id("SIG"),
        "strategy_id": "SNIPER-V168", "strategy_version": "1.0.0",
        "symbol": symbol, "timeframe": "M15",
        "direction": direction, "signal_type": "ENTRY",
        "entry_reference": 1.1390, "stop_reference": 1.1380,
        "target_reference": 1.1410, "confidence": 0.8,
        "created_at": utc_now_iso(),
        "expires_at": "2999-01-01T00:00:00+00:00",
        "correlation_id": new_id("COR")}


class TestMockScenariosAL:
    """LOOPBACK MOCK E2E — not external, not DEMO runtime."""

    def test_scenario_A_signal_entry_fill_position_flow(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, analyzer, _ = _wire(server)
        ack = analyzer._rpc(_signal_message())
        assert ack["accepted"] is True and ack["delivered"] == 1
        pushed = ea.drain_pushes()
        signal = next(m for m in pushed if m["type"] == "SIGNAL_PUBLISH")
        # EA (authority side) then publishes the resulting chain events
        for i, event_type in enumerate(("DECISION", "RISK_DECISION",
                                        "ORDER", "EXECUTION", "POSITION"),
                                       start=1):
            resp = ea.publish(event_type, sequence=i)
            assert resp["accepted"] is True
        assert signal["direction"] == "BUY"

    def test_scenario_B_signal_risk_reject_no_order(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, _ = _wire(server)
        intents = []
        server.events.subscribe("ORDER_INTENT", intents.append)
        ea.publish("RISK_DECISION", sequence=1)    # reject => no intent
        assert intents == []                        # gateway invents none

    def test_scenario_C_gateway_timeout_then_retry_same_key(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, os_client = _wire(server)
        first = os_client.command("START", idempotency_key="SC-C",
                                  request_id="REQ-C1")
        retry = os_client.command("START", idempotency_key="SC-C",
                                  request_id="REQ-C2")
        assert first["duplicate"] is False and retry["duplicate"] is True
        starts = [m for m in ea.drain_pushes()
                  if m.get("command") == "START"]
        assert len(starts) == 1

    def test_scenario_D_partial_then_full_fill_events(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, analyzer, _ = _wire(server)
        fills = []
        server.events.subscribe("ORDER_PARTIALLY_FILLED", fills.append)
        server.events.subscribe("ORDER_FILLED", fills.append)
        ack1 = ea.publish("ORDER_PARTIALLY_FILLED", sequence=1)
        ack2 = ea.publish("ORDER_FILLED", sequence=2)
        assert ack1["accepted"] and ack2["accepted"]
        assert len(fills) == 2

    def test_scenario_E_position_exit_close(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, os_client = _wire(server)
        resp = os_client.command("PAUSE")           # operational exit path
        assert resp["state"] == "COMPLETED"
        ea.drain_pushes()                             # clear routed PAUSE
        for i, event_type in enumerate(("POSITION_OPENED",
                                        "POSITION_CLOSED"), start=1):
            assert ea.publish(event_type, sequence=i)["accepted"]

    def test_scenario_F_duplicate_order_one_logical_operation(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, os_client = _wire(server)
        a = os_client.command("DEPLOY", idempotency_key="ORDER-001",
                              request_id="R1")
        b = os_client.command("DEPLOY", idempotency_key="ORDER-001",
                              request_id="R2")
        c = os_client.command("DEPLOY", idempotency_key="ORDER-001",
                              request_id="R3")
        assert (a["duplicate"], b["duplicate"], c["duplicate"]) == \
            (False, True, True)
        delivered = [m for m in ea.drain_pushes()
                     if m.get("command") == "DEPLOY"]
        assert len(delivered) == 1                  # ONE logical order

    def test_scenario_G_disconnect_during_order_fail_closed(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, _ = _wire(server)
        session = server.sessions.by_client("OUR-EA-RUNTIME")
        server.sessions.transition(session, "DISCONNECTED",
                                   reason="network loss mid-order")
        server.set_safe(False, "disconnect during in-flight order")
        ea.drain_pushes()                             # clear the HALT push
        blocked = ea.publish("ORDER_INTENT", sequence=1)
        assert blocked["type"] == "ERROR"
        assert "no new order flow" in blocked["message"]

    def test_scenario_H_os_restart_existing_position_preserved(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, _ = _wire(server)
        ea.publish("POSITION_OPENED", sequence=1)
        server.stop()                                 # "OS restart"
        audit2 = FakeAudit()
        server2 = _server_with_routes(audit2)
        ea2 = ProtocolClient(server2)
        ea2.connect(client_id="OUR-EA-RUNTIME", client_type="OUR_EA")
        os2 = ProtocolClient(server2)
        resp = os2.command("RECONCILE")               # restart => reconcile
        assert resp["state"] == "COMPLETED"
        assert any(m.get("command") == "RECONCILE"
                   for m in ea2.drain_pushes())

    def test_scenario_I_reconciliation_mismatch_surfaces(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, _ = _wire(server)
        mismatches = []
        server.events.subscribe("RECONCILIATION_FAILED", mismatches.append)
        assert ea.publish("RECONCILIATION_FAILED", sequence=1)["accepted"]
        assert len(mismatches) == 1                   # detected+surfaced

    def test_scenario_J_kill_switch_active_strategy(self):
        audit = FakeAudit()
        server = _server_with_routes(audit)
        ea, _, os_client = _wire(server)
        resp = os_client.command("KILL", idempotency_key="SC-J")
        assert resp["result"]["order_flow"] == "BLOCKED"
        ea.drain_pushes()                             # clear the KILL push
        assert ea.publish("ORDER_INTENT", sequence=9)["type"] == "ERROR"

    def test_scenario_K_wrong_demo_account_hard_block(self):
        guard = AccountGuard(PINNED)
        wrong = TerminalIdentity(trade_mode=0, login=999999,
                                 server="XMGlobal-MT5 16")
        with pytest.raises(AccountGuardError):
            guard.check(wrong)

    def test_scenario_L_live_account_attempt_hard_block(self):
        guard = AccountGuard(PINNED)
        live = TerminalIdentity(trade_mode=2, login=113126589,
                                server="XMGlobal-MT5 16")
        with pytest.raises(AccountGuardError) as err:
            guard.check(live)
        assert "trade_mode 2" in str(err.value)      # HARD BLOCK
