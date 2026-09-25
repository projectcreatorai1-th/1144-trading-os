"""Phase 5 EMS + adapters tests: routing, environment isolation, timeout/
UNKNOWN, retry classification, capability gating, MT5 mapping, outbox."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

import pytest

from adapters.mt5.execution import MT5ExecutionAdapter
from adapters.mt5.transport import MT5Transport, MT5TransportResult
from adapters.simulation.execution import SimulationExecutionAdapter
from adapters.simulation.sequencer import SimulationSequencer
from architecture.contracts.errors import ContractError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from core.ems.adapter import (
    AdapterCapability,
    AdapterResponse,
    ConnectionState,
    PositionSemantics,
)
from core.ems.engine import ExecutionManagementSystem
from core.ems.outbox import DeliveryStatus, OutboxMessage, OutboxStore
from core.execution.boundary import ExecutionContext
from core.execution.contracts import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from core.oms.contracts import BrokerError, classify_retry
from core.risk.contracts import RiskResult
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.factories import make_risk_decision
from tests.phase1_factories import at
from tests.test_phase3_risk_engine import activate as activate_policy, full_context
from tests.test_phase5_boundary_oms import aligned, ctx, make_order


class MemoryOutbox(OutboxStore):
    def __init__(self):
        self.messages: dict[str, OutboxMessage] = {}

    def append(self, message):
        message.validate()
        self.messages[message.message_id] = message

    def mark_delivered(self, message_id):
        m = self.messages[message_id]
        self.messages[message_id] = OutMessage_replace(m, DeliveryStatus.DELIVERED)

    def mark_failed(self, message_id):
        m = self.messages[message_id]
        self.messages[message_id] = OutMessage_replace(m, DeliveryStatus.FAILED)

    def increment_attempt(self, message_id):
        m = self.messages[message_id]
        self.messages[message_id] = OutMessage_replace(m, m.delivery_status, m.attempt_count + 1)

    def iter_pending(self):
        return iter([m for m in self.messages.values()
                     if m.delivery_status is DeliveryStatus.PENDING])

    def get(self, message_id):
        return self.messages[message_id]


def OutMessage_replace(m, status, attempts=None):
    from dataclasses import replace
    return replace(m, delivery_status=status,
                   attempt_count=m.attempt_count if attempts is None else attempts)


@dataclass
class ScriptedTransport(MT5Transport):
    """Controlled MT5 terminal responses (test scope, deterministic)."""
    script: list[MT5TransportResult] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    account: dict = field(default_factory=lambda: {
        "margin_mode": 0, "symbols": ("XAUUSD",), "min_volume": "0.01",
        "max_volume": "100", "volume_step": "0.01", "digits": 2,
    })
    state: ConnectionState = ConnectionState.DISCONNECTED

    def connect(self):
        self.state = ConnectionState.CONNECTED

    def disconnect(self):
        self.state = ConnectionState.DISCONNECTED

    def connection_state(self):
        return self.state

    def account_info(self):
        return self.account

    def order_send(self, mt5_request):
        self.requests.append(dict(mt5_request))
        return self.script.pop(0) if self.script else MT5TransportResult(
            True, {"retcode": 10009, "ticket": 12345, "reference": "MT5-12345"})

    def order_cancel(self, params):
        self.requests.append(dict(params))
        return MT5TransportResult(True, {"retcode": 10009, "ticket": params["ticket"]})

    def order_get(self, params):
        return MT5TransportResult(True, {"retcode": "OK", "ticket": params["ticket"],
                                         "state": "FILLED"})


class TestRetryClassification:
    def test_retryable(self):
        assert classify_retry(BrokerError.TIMEOUT) is not None
        assert classify_retry(BrokerError.TIMEOUT).value == "RETRYABLE"
        assert classify_retry(BrokerError.CONNECTION_ERROR).value == "RETRYABLE"

    def test_non_retryable(self):
        assert classify_retry(BrokerError.INVALID_VOLUME).value == "NON_RETRYABLE"
        assert classify_retry(BrokerError.INSUFFICIENT_MARGIN).value == "NON_RETRYABLE"

    def test_unknown(self):
        assert classify_retry(BrokerError.REQUOTE).value == "UNKNOWN"
        assert classify_retry(BrokerError.UNKNOWN).value == "UNKNOWN"


class TestSimulationAdapter:
    def test_submit_ok(self):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        assert adapter.health() is ConnectionState.CONNECTED
        response = adapter.submit_order({
            "order_id": "ord_1", "symbol": "XAUUSD", "side": "BUY",
            "order_type": "MARKET", "quantity": "1.00", "price": "2650.00",
            "time_in_force": "GTC", "environment": "SIMULATION", "idempotency_key": "k1",
        })
        response.validate()
        assert response.ok
        assert response.raw["outcome"]["synthetic"] is True  # explicit synthetic evidence

    def test_environment_is_simulations(self):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        assert adapter.environment() == "SIMULATION"  # never called DEMO

    def test_capability_valid(self):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        capability = adapter.capabilities()
        capability.validate()
        assert capability.execution_safe


class TestMT5Mapping:
    def test_canonical_to_mt5_mapping(self):
        transport = ScriptedTransport()
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        response = adapter.submit_order({
            "order_id": "ord_1", "symbol": "XAUUSD", "side": "BUY",
            "order_type": "LIMIT", "quantity": "1.00", "price": "2650.00",
            "time_in_force": "GTC", "environment": "DEMO", "idempotency_key": "k1",
        })
        assert response.ok
        request = transport.requests[0]
        assert request["symbol"] == "XAUUSD"
        assert request["type"] == 0  # BUY
        assert request["action"] == 1  # LIMIT
        assert request["volume"] == 1.0

    def test_mt5_response_normalized_raw_preserved(self):
        transport = ScriptedTransport(script=[
            MT5TransportResult(False, {"retcode": 10014, "comment": "Invalid volume"}),
        ])
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        response = adapter.submit_order({
            "order_id": "ord_1", "symbol": "XAUUSD", "side": "BUY",
            "order_type": "MARKET", "quantity": "1.00",
            "time_in_force": "GTC", "environment": "DEMO", "idempotency_key": "k1",
        })
        assert not response.ok
        assert response.normalized_error == "INVALID_VOLUME"  # canonical taxonomy
        assert response.raw["mt5_result"]["retcode"] == 10014  # raw evidence preserved

    def test_mt5_types_never_leak(self):
        transport = ScriptedTransport()
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        response = adapter.submit_order({
            "order_id": "ord_1", "symbol": "XAUUSD", "side": "SELL",
            "order_type": "MARKET", "quantity": "1.00",
            "time_in_force": "GTC", "environment": "DEMO", "idempotency_key": "k1",
        })
        # response carries only canonical mapping + raw dict (no MT5 objects)
        assert isinstance(response.raw, Mapping)
        assert response.broker_order_id == "12345"

    def test_live_requires_explicit_environment(self):
        with pytest.raises(ValueError):
            MT5ExecutionAdapter(ScriptedTransport(), environment="SIMULATION")

    def test_capability_from_account(self):
        adapter = MT5ExecutionAdapter(ScriptedTransport(), environment="DEMO")
        capability = adapter.capabilities()
        capability.validate()
        assert capability.position_semantics is PositionSemantics.NETTING
        assert capability.provenance["observed"] is True


class TestEMS:
    def _ems(self, adapters, outbox=None):
        return ExecutionManagementSystem(adapters=adapters, outbox=outbox)

    def _setup(self, environment="SIMULATION", adapter=None):
        adapter = adapter or SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        ems = self._ems({environment: adapter})
        risk = make_risk_decision(environment=environment, scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment=environment), risk)
        order = Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})
        return ems, order, risk, adapter

    def test_submit_via_simulation(self):
        ems, order, risk, _ = self._setup()
        result = ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert result.submitted
        assert result.state == "ACKNOWLEDGED"

    def test_missing_adapter_fails_closed(self):
        ems = self._ems({})  # nothing registered
        risk = make_risk_decision(environment="PAPER", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="PAPER"), risk)
        order = Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})
        with pytest.raises(ContractError) as excinfo:
            ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert excinfo.value.rule_id == "ENV-004"  # never falls back to another env

    def test_risk_block_prevents_submission(self):
        ems, order, risk, _ = self._setup()
        blocked = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                     decision=RiskResult.BLOCK,
                                     decision_time=at(11, 59), expires_at=at(12, 30))
        blocked_order = aligned(make_order(), blocked)
        blocked_order = Order(**{**blocked_order.__dict__, "status": OrderStatus.ACCEPTED})
        with pytest.raises(RiskGateError):
            ems.submit(blocked_order, risk_decision=blocked, execution_context=ctx())

    def test_timeout_goes_unknown_not_failed(self):
        transport = ScriptedTransport(script=[
            MT5TransportResult(False, {"retcode": 10012, "comment": "request timed out"}),
        ])
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        adapter.connect()
        ems, order, risk, _ = self._setup(environment="DEMO", adapter=adapter)
        result = ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert result.state == "UNKNOWN"  # timeout != failure (SECTION 26/27)
        assert result.normalized_error == "TIMEOUT"
        assert result.retry_classification == "RETRYABLE"

    def test_unknown_market_state_blocks_submission(self):
        ems, order, risk, _ = self._setup()
        with pytest.raises(RiskGateError):
            ems.submit(order, risk_decision=risk,
                       execution_context=ctx(market_state="UNKNOWN"))

    def test_unknown_capability_blocks(self):
        class UnknownSemanticsAdapter(SimulationExecutionAdapter):
            def capabilities(self):
                capability = super().capabilities()
                from dataclasses import replace
                return replace(capability, position_semantics=PositionSemantics.UNKNOWN)

        adapter = UnknownSemanticsAdapter(SimulationSequencer())
        adapter.connect()
        ems = self._ems({"SIMULATION": adapter})
        risk = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), risk)
        order = Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})
        with pytest.raises(RiskGateError) as excinfo:
            ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert excinfo.value.rule_id == "EXEC-003"

    def test_unsupported_symbol_blocks(self):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        ems = self._ems({"SIMULATION": adapter})
        risk = make_risk_decision(environment="SIMULATION", scope="BTCUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        # capability supports "*" so use MT5 with explicit symbols instead
        transport = ScriptedTransport()
        transport.account["symbols"] = ("EURUSD",)
        mt5 = MT5ExecutionAdapter(transport, environment="DEMO")
        mt5.connect()
        ems = self._ems({"DEMO": mt5})
        risk = make_risk_decision(environment="DEMO", scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment="DEMO", symbol="XAUUSD"), risk)
        order = Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})
        with pytest.raises(RiskGateError):
            ems.submit(order, risk_decision=risk, execution_context=ctx())

    def test_outbox_written_on_submission(self):
        outbox = MemoryOutbox()
        ems, order, risk, _ = self._setup()
        ems_with_outbox = ExecutionManagementSystem(
            adapters={"SIMULATION": SimulationExecutionAdapter(SimulationSequencer())},
            outbox=outbox)
        adapter = list(ems_with_outbox._adapters.values())[0]
        adapter.connect()
        ems_with_outbox.submit(order, risk_decision=risk, execution_context=ctx())
        assert len(outbox.messages) == 1
        message = list(outbox.messages.values())[0]
        assert message.event_type == "ORDER_SUBMIT"
        assert message.delivery_status is DeliveryStatus.PENDING

    def test_poll_for_reconciliation(self):
        transport = ScriptedTransport()
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        adapter.connect()
        ems = self._ems({"DEMO": adapter})
        response = ems.poll(Order(**{**aligned(
            make_order(environment="DEMO"),
            make_risk_decision(environment="DEMO", scope="XAUUSD",
                               decision=RiskResult.ALLOW,
                               decision_time=at(11, 59), expires_at=at(12, 30))
        ).__dict__, "status": OrderStatus.UNKNOWN}), "12345")
        assert response.ok


class TestEnvironmentIsolation:
    @pytest.mark.parametrize("order_env,adapter_env", [
        ("SIMULATION", "PAPER"), ("SIMULATION", "DEMO"), ("SIMULATION", "LIVE"),
        ("PAPER", "DEMO"), ("PAPER", "LIVE"), ("DEMO", "LIVE"),
    ])
    def test_cross_environment_rejected(self, order_env, adapter_env):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={adapter_env: adapter})
        risk = make_risk_decision(environment=order_env, scope="XAUUSD",
                                  decision=RiskResult.ALLOW,
                                  decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(environment=order_env), risk)
        order = Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})
        with pytest.raises(ContractError):
            ems.submit(order, risk_decision=risk, execution_context=ctx())
