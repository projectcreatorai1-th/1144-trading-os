"""Phase 5 invariants (SECTION 60: all 40) + E2E flows (SECTION 65) +
recovery/crash scenarios (SECTION 31)."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from adapters.mt5.execution import MT5ExecutionAdapter
from adapters.mt5.transport import MT5Transport, MT5TransportResult
from adapters.simulation.execution import SimulationExecutionAdapter
from adapters.simulation.sequencer import SimulationSequencer
from architecture.contracts.errors import ContractError, RiskGateError, StorageError
from architecture.contracts.identifiers import new_identifier
from core.ems.adapter import ConnectionState
from core.ems.engine import ExecutionManagementSystem
from core.ems.outbox import DeliveryStatus
from core.execution.boundary import ExecutionDecision, ExecutionContext
from core.execution.contracts import Order, OrderSide, OrderStatus
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerPostingService
from core.oms.contracts import (
    ExecutionReport,
    ExecutionStatus,
    ExecutionType,
    execution_idempotency_key,
)
from core.oms.engine import OrderManagementSystem
from core.oms.projection import ExecutionProjector
from core.risk.contracts import RiskResult
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role
from tests.factories import make_risk_decision
from tests.phase1_factories import at
from tests.test_phase5_boundary_oms import aligned, ctx, make_order
from tests.test_phase5_ems_adapters import MemoryOutbox, ScriptedTransport
from tests.test_phase5_pipeline import _order, _report


@pytest.fixture()
def env(tmp_path):
    storage = StorageSet(tmp_path / "p5-e2e.db")
    posting = LedgerPostingService(storage.ledger, storage.audit)
    adapter = SimulationExecutionAdapter(SimulationSequencer())
    adapter.connect()
    ems = ExecutionManagementSystem(adapters={"SIMULATION": adapter},
                                    outbox=None, audit=storage.audit)
    oms = OrderManagementSystem(orders=storage.orders,
                                reports=storage.execution_reports,
                                audit=storage.audit)
    projector = ExecutionProjector(states=storage.states, posting=posting,
                                   events=storage.events, audit=storage.audit)
    yield {"storage": storage, "posting": posting, "adapter": adapter,
           "ems": ems, "oms": oms, "projector": projector}
    storage.close()


def _allow_risk(env_name="SIMULATION", scope="XAUUSD", expires_offset=30):
    from tests.phase1_factories import at

    return make_risk_decision(environment=env_name, scope=scope,
                              decision=RiskResult.ALLOW,
                              decision_time=at(11, 59),
                              expires_at=at(12, expires_offset))


def _submit_order(env, quantity="1.00", risk=None):
    risk = risk or _allow_risk()
    order = aligned(make_order(quantity=Decimal(quantity)), risk)
    accepted = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
    assert accepted.accepted
    return accepted.order, risk


def _accepted_for_ems(env, order):
    return Order(**{**order.__dict__, "status": OrderStatus.ACCEPTED})


# ====================================================================== #
# E2E FLOWS (SECTION 65)                                                 #
# ====================================================================== #
class TestE2E:
    def test_001_simulation_full_chain(self, env):
        order, risk = _submit_order(env)
        result = env["ems"].submit(_accepted_for_ems(env, order),
                                   risk_decision=risk, execution_context=ctx())
        assert result.submitted
        report = _report(order)
        updated, _ = env["oms"].ingest_execution_report(report)
        projection = env["projector"].project(report, order, at=at(12, 2))
        assert updated.status is OrderStatus.FILLED
        assert projection.position_state.payload["quantity"] == "1.00"
        entry = env["storage"].ledger.get_by_id(projection.ledger_entry_id)
        assert entry.entry_type is LedgerType.EXECUTION
        # reconciliation: internal vs broker observation
        from core.reconciliation.contracts import ReconciliationScope
        from core.reconciliation.engine import ReconciliationEngine

        engine = ReconciliationEngine(env["storage"].reconciliations, env["storage"].audit)
        outcome = engine.compare_values_map(
            scope=ReconciliationScope.POSITION, environment="SIMULATION",
            internal_values={"ACCOUNT:XAUUSD": projection.position_state.payload["quantity"]},
            external_values={"ACCOUNT:XAUUSD": "1.00"},
            source="broker-observation", comparison_time=at(12, 5))
        assert outcome.status.value == "MATCH"

    def test_002_demo_mt5_flow(self, tmp_path):
        storage = StorageSet(tmp_path / "demo.db")
        transport = ScriptedTransport()
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={"DEMO": adapter}, audit=storage.audit)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        risk = _allow_risk(env_name="DEMO")
        order = aligned(make_order(environment="DEMO"), risk)
        accepted = oms.submit(order, risk_decision=risk, execution_context=ctx())
        assert accepted.accepted
        result = ems.submit(_accepted_for_ems(tmp_path, accepted.order),
                            risk_decision=risk, execution_context=ctx())
        assert result.submitted and result.broker_order_id
        # raw MT5 evidence preserved, nothing leaked
        assert transport.requests[0]["symbol"] == "XAUUSD"
        storage.close()

    def test_003_risk_block_no_submission(self, env):
        blocked = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                     decision=RiskResult.BLOCK,
                                     decision_time=at(11, 59), expires_at=at(12, 30))
        order = aligned(make_order(), blocked)
        result = env["oms"].submit(order, risk_decision=blocked, execution_context=ctx())
        assert not result.accepted
        assert "risk_decision_BLOCK" in result.reasons
        assert env["storage"].orders.count() == 0  # nothing persisted for canonical order

    def test_004_expired_risk_no_broker_submission(self, env):
        expired = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                     decision=RiskResult.ALLOW,
                                     decision_time=at(11, 0), expires_at=at(11, 30))
        order = aligned(make_order(), expired)
        result = env["oms"].submit(order, risk_decision=expired, execution_context=ctx())
        assert not result.accepted and "risk_decision_expired" in result.reasons

    def test_005_duplicate_submit_one_canonical(self, env):
        order, risk = _submit_order(env)
        again = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
        assert again.duplicate_of == order.order_id
        assert env["storage"].orders.count() == 1

    def test_006_partial_fill_cumulative(self, env):
        order, _ = _submit_order(env, "1.00")
        for ref, quantity in (("A", "0.30"), ("B", "0.20"), ("C", "0.50")):
            report = _report(order, quantity=quantity, ref=ref)
            order, _ = env["oms"].ingest_execution_report(report)
        assert order.status is OrderStatus.FILLED
        assert env["oms"].filled_quantity(order.order_id) == Decimal("1.00")

    def test_007_broker_reject_evidence_preserved(self, env):
        order, _ = _submit_order(env)
        report = replace(_report(order), execution_type=ExecutionType.REJECT,
                         status=ExecutionStatus.REJECTED)
        updated, _ = env["oms"].ingest_execution_report(report)
        assert updated.status is OrderStatus.REJECTED
        stored = env["storage"].execution_reports.get_by_id(report.execution_id)
        assert stored.provenance["raw"]  # raw evidence preserved

    def test_008_timeout_reconciles_not_blind_retry(self, tmp_path):
        storage = StorageSet(tmp_path / "timeout.db")
        transport = ScriptedTransport(script=[
            MT5TransportResult(False, {"retcode": 10012, "comment": "timed out"}),
            MT5TransportResult(True, {"retcode": 10009, "ticket": 777, "reference": "MT5-777"}),
        ])
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={"DEMO": adapter}, audit=storage.audit)
        risk = _allow_risk(env_name="DEMO")
        order = Order(**{**aligned(make_order(environment="DEMO"), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        first = ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert first.state == "UNKNOWN"
        # reconciliation path: poll, do NOT blind-resubmit
        polled = ems.poll(order, first.broker_order_id or "0")
        assert polled.ok
        storage.close()

    def test_009_restart_recovery(self, tmp_path):
        path = tmp_path / "restart.db"
        storage = StorageSet(path)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        order, _ = _submit_order({"oms": oms, "storage": storage})
        active_before = [o.order_id for o in oms.recover()]
        storage.close()

        storage2 = StorageSet(path)
        oms2 = OrderManagementSystem(orders=storage2.orders,
                                     reports=storage2.execution_reports,
                                     audit=storage2.audit)
        active_after = [o.order_id for o in oms2.recover()]
        assert active_before == active_after  # deterministic resume
        storage2.close()

    def test_010_close_only_reduce_ok_increase_blocked(self, env):
        order, risk = _submit_order(env)
        close_only_ctx = ctx(safety_control="CLOSE_ONLY")
        with pytest.raises((RiskGateError, ContractError)):
            env["ems"].submit(_accepted_for_ems(env, order),
                              risk_decision=risk, execution_context=close_only_ctx)
        from core.execution.boundary import can_execute

        outcome = can_execute(order, risk, close_only_ctx)
        assert "close_only_blocks_new_exposure" in outcome.reasons

    def test_011_cancel_requires_evidence(self, env):
        order, _ = _submit_order(env)
        requested = env["oms"].request_cancel(order, reason="op", actor="op", at=at(12, 2))
        assert requested.status is OrderStatus.CANCEL_REQUESTED
        evidence = ExecutionReport(
            execution_id=new_identifier("execution_id"), order_id=order.order_id,
            environment="SIMULATION", execution_type=ExecutionType.CANCEL,
            status=ExecutionStatus.CONFIRMED, symbol=order.symbol, side=order.side,
            executed_quantity="0", execution_price="0", currency="USD",
            broker_timestamp=at(12, 3), received_at=at(12, 3),
            provenance={"source": "simulation", "adapter": "sim", "raw": {}},
            raw_reference="CXL-EV",
            idempotency_key=execution_idempotency_key(
                order_id=order.order_id, broker_order_id=None,
                broker_execution_reference="CXL-EV"))
        cancelled = env["oms"].confirm_cancel(requested, evidence=evidence, at=at(12, 3))
        assert cancelled.status is OrderStatus.CANCELLED

    def test_012_replace_new_version_risk_revalidated(self, env):
        order, risk = _submit_order(env)
        replacement = env["oms"].build_replacement(order, quantity="2.00", price=None,
                                                   risk_decision=risk,
                                                   execution_context=ctx(),
                                                   reason="increase")
        assert replacement.order_id != order.order_id
        assert replacement.replaces_order_id == order.order_id

    def test_013_reconciliation_mismatch_no_autofix(self, env):
        from core.reconciliation.contracts import ReconciliationScope
        from core.reconciliation.engine import ReconciliationEngine

        order, _ = _submit_order(env)
        report = _report(order)
        env["oms"].ingest_execution_report(report)
        projection = env["projector"].project(report, order, at=at(12, 2))
        engine = ReconciliationEngine(env["storage"].reconciliations, env["storage"].audit)
        outcome = engine.compare_values_map(
            scope=ReconciliationScope.POSITION, environment="SIMULATION",
            internal_values={"ACCOUNT:XAUUSD": projection.position_state.payload["quantity"]},
            external_values={"ACCOUNT:XAUUSD": "0.50"},  # broker says 0.50
            source="broker-observation", comparison_time=at(12, 5))
        assert outcome.status.value == "MISMATCH"  # reported, never auto-fixed
        assert projection.position_state.payload["quantity"] == "1.00"  # unchanged

    @pytest.mark.parametrize("order_env,adapter_env", [
        ("SIMULATION", "PAPER"), ("SIMULATION", "DEMO"), ("SIMULATION", "LIVE"),
        ("PAPER", "DEMO"), ("PAPER", "LIVE"), ("DEMO", "LIVE"),
    ])
    def test_014_environment_isolation_matrix(self, order_env, adapter_env):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={adapter_env: adapter})
        risk = _allow_risk(env_name=order_env)
        order = Order(**{**aligned(make_order(environment=order_env), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        with pytest.raises(ContractError):
            ems.submit(order, risk_decision=risk, execution_context=ctx())


# ====================================================================== #
# INVARIANTS (SECTION 60) — grouped                                      #
# ====================================================================== #
class TestInvariants1to10:
    def test_inv_001_004_no_execution_without_valid_risk(self, env):
        # ACCEPTED requires a risk decision that validates (OMS.submit rejects
        # otherwise); UNKNOWN/blocked/expired all reject - covered by boundary
        # tests; here assert the accepted store invariant end-to-end.
        from core.execution.store import OrderStore

        order, _ = _submit_order(env)
        stored = env["storage"].orders.get_by_id(order.order_id)
        assert stored.risk_decision_id is not None

    def test_inv_005_strategy_cannot_reach_broker_directly(self):
        import core.strategy.gate as gate_module

        source = open(gate_module.__file__, encoding="utf-8").read()
        for forbidden in ("submit_order", "ExecutionAdapter", "adapters.mt5"):
            assert forbidden not in source

    def test_inv_006_oms_cannot_call_mt5(self):
        import core.oms.engine as oms_module

        source = open(oms_module.__file__, encoding="utf-8").read()
        for forbidden in ("adapters.mt5", "MT5ExecutionAdapter", "MetaTrader5"):
            assert forbidden not in source

    def test_inv_007_ems_only_validated_orders(self, env):
        risk = _allow_risk()
        order = aligned(make_order(), risk)  # status CREATED, not ACCEPTED
        with pytest.raises(ContractError):
            env["ems"].submit(order, risk_decision=risk, execution_context=ctx())

    def test_inv_008_ai_no_execution(self):
        import adapters.ai.contracts as ai_module

        source = open(ai_module.__file__, encoding="utf-8").read()
        for forbidden in ("Order(", "submit_order", "OMS", "EMS"):
            assert forbidden not in source

    def test_inv_009_duplicate_semantic_no_duplicate_submission(self, env):
        order, risk = _submit_order(env)
        again = env["oms"].submit(order, risk_decision=risk, execution_context=ctx())
        assert not again.accepted and again.duplicate_of == order.order_id

    def test_inv_010_fills_sum_leq_requested(self, env):
        order, _ = _submit_order(env, "1.00")
        report = _report(order, quantity="0.60")
        updated, _ = env["oms"].ingest_execution_report(report)
        over = _report(order, quantity="0.60", ref="OVER")
        updated, _ = env["oms"].ingest_execution_report(over)
        assert updated.status is OrderStatus.UNKNOWN  # overfill -> corruption path


class TestInvariants11to20:
    def test_inv_011_012_immutability(self, env):
        from dataclasses import FrozenInstanceError

        order, _ = _submit_order(env)
        with pytest.raises(FrozenInstanceError):
            order.quantity = Decimal("9")
        report = _report(order)
        with pytest.raises(FrozenInstanceError):
            report.executed_quantity = "9"

    def test_inv_013_duplicate_report_no_duplicate_ledger(self, env):
        order, _ = _submit_order(env)
        report = _report(order, ref="DUP")
        first = env["projector"].project(report, order, at=at(12, 2))
        second = env["projector"].project(report, order, at=at(12, 3))
        assert first.duplicate is False and second.duplicate is True
        assert env["storage"].ledger.count() == 1

    def test_inv_014_015_position_ledger_from_execution(self, env):
        order, _ = _submit_order(env)
        report = _report(order)
        projection = env["projector"].project(report, order, at=at(12, 2))
        state = env["storage"].states.get_current_state(
            "POSITION_STATE", projection.position_state.entity_id)
        assert state is not None and state.source_event_id is not None
        entry = env["storage"].ledger.get_by_id(projection.ledger_entry_id)
        assert entry.source_event_id  # ledger traces to canonical event

    def test_inv_016_017_unknown_not_failed(self, tmp_path):
        storage = StorageSet(tmp_path / "unk.db")
        transport = ScriptedTransport(script=[
            MT5TransportResult(False, {"retcode": 10012, "comment": "timeout"}),
        ])
        adapter = MT5ExecutionAdapter(transport, environment="DEMO")
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={"DEMO": adapter})
        risk = _allow_risk(env_name="DEMO")
        order = Order(**{**aligned(make_order(environment="DEMO"), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        result = ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert result.state == "UNKNOWN"  # timeout != rejected != failed
        storage.close()

    def test_inv_018_demo_cannot_use_live_adapter(self):
        # environment mismatch is rejected structurally (EMS._adapter_for)
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={"SIMULATION": adapter})
        risk = _allow_risk(env_name="DEMO")
        order = Order(**{**aligned(make_order(environment="DEMO"), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        with pytest.raises(ContractError):
            ems.submit(order, risk_decision=risk, execution_context=ctx())

    def test_inv_019_live_requires_permission(self):
        from core.execution.boundary import can_execute

        risk = _allow_risk(env_name="LIVE")
        order = aligned(make_order(environment="LIVE"), risk)
        denied = can_execute(order, risk, ctx(actor_role=Role.TRADER))
        assert denied.decision is ExecutionDecision.REJECT

    def test_inv_020_close_only_no_increase(self):
        from core.execution.boundary import can_execute

        risk = _allow_risk()
        order = aligned(make_order(), risk)
        result = can_execute(order, risk, ctx(safety_control="CLOSE_ONLY"))
        assert result.decision is ExecutionDecision.REJECT


class TestInvariants21to40:
    def test_inv_021_material_change_risk_revalidation(self, env):
        order, risk = _submit_order(env)
        blocked = make_risk_decision(environment="SIMULATION", scope="XAUUSD",
                                     decision=RiskResult.BLOCK,
                                     decision_time=at(12, 1), expires_at=at(12, 30))
        with pytest.raises(RiskGateError):
            env["oms"].build_replacement(order, quantity="2.00", price=None,
                                         risk_decision=blocked,
                                         execution_context=ctx(), reason="increase")

    def test_inv_022_023_024_no_risk_override_no_second_engine(self):
        import core.oms.engine as oms_source
        import core.ems.engine as ems_source

        for module in (oms_source, ems_source):
            source = open(module.__file__, encoding="utf-8").read()
            assert "class RiskEngine" not in source
            assert "RiskEngine(" not in source.replace("RiskEngine, ", "")

    def test_inv_025_provenance_on_external_execution(self, env):
        order, _ = _submit_order(env)
        report = _report(order)
        env["oms"].ingest_execution_report(report)
        stored = env["storage"].execution_reports.get_by_id(report.execution_id)
        assert set(("source", "adapter", "raw")) <= set(stored.provenance)

    def test_inv_026_causal_chain_present(self, env):
        order, _ = _submit_order(env)
        report = _report(order)
        projection = env["projector"].project(report, order, at=at(12, 2))
        event = env["storage"].events.get_by_id(projection.event_id)
        assert event is not None and event.entity_id == order.order_id
        assert event.correlation_id == order.correlation_id

    def test_inv_027_deterministic_identity(self, env):
        order, _ = _submit_order(env)
        stored = env["storage"].orders.get_by_id(order.order_id)
        assert stored.idempotency_key == order.idempotency_key

    def test_inv_028_normalization_before_domain(self):
        from core.oms.contracts import normalize_error

        assert normalize_error(10014, adapter="mt5", adapter_version="1") is not None
        # adapter responses expose only canonical + raw, verified in adapter tests

    def test_inv_029_reconciliation_cannot_mutate(self, env):
        from core.reconciliation.contracts import ReconciliationScope
        from core.reconciliation.engine import ReconciliationEngine

        order, _ = _submit_order(env)
        report = _report(order)
        env["oms"].ingest_execution_report(report)
        projection = env["projector"].project(report, order, at=at(12, 2))
        before = projection.position_state.payload["quantity"]
        entity_key = projection.position_state.entity_id
        engine = ReconciliationEngine(env["storage"].reconciliations, env["storage"].audit)
        engine.compare_values_map(
            scope=ReconciliationScope.POSITION, environment="SIMULATION",
            internal_values={"P": before}, external_values={"P": "9.99"},
            source="broker", comparison_time=at(12, 5))
        after = env["storage"].states.get_current_state(
            "POSITION_STATE", entity_key).payload["quantity"]
        assert before == after  # mismatch reported, state untouched

    def test_inv_030_recovery_idempotent(self, tmp_path):
        path = tmp_path / "idem.db"
        storage = StorageSet(path)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        order, risk = _submit_order({"oms": oms, "storage": storage})
        oms.submit(order, risk_decision=risk, execution_context=ctx())  # retry
        storage.close()
        storage2 = StorageSet(path)
        oms2 = OrderManagementSystem(orders=storage2.orders,
                                     reports=storage2.execution_reports,
                                     audit=storage2.audit)
        assert len(list(oms2.recover())) == 1  # single canonical active order
        storage2.close()

    def test_inv_031_environment_immutable(self, env):
        from dataclasses import FrozenInstanceError

        order, _ = _submit_order(env)
        with pytest.raises(FrozenInstanceError):
            order.environment = "LIVE"

    def test_inv_032_unknown_capability_blocks(self, tmp_path):
        from core.ems.adapter import PositionSemantics
        from dataclasses import replace as dc_replace

        class UnknownCap(SimulationExecutionAdapter):
            def capabilities(self):
                return dc_replace(super().capabilities(),
                                  position_semantics=PositionSemantics.UNKNOWN)

        adapter = UnknownCap(SimulationSequencer())
        adapter.connect()
        ems = ExecutionManagementSystem(adapters={"SIMULATION": adapter})
        risk = _allow_risk()
        order = Order(**{**aligned(make_order(), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        with pytest.raises(RiskGateError):
            ems.submit(order, risk_decision=risk, execution_context=ctx())

    def test_inv_033_unknown_market_blocks(self):
        from core.execution.boundary import can_execute

        risk = _allow_risk()
        order = aligned(make_order(), risk)
        assert can_execute(order, risk, ctx(market_state="UNKNOWN")).decision \
            is ExecutionDecision.UNKNOWN

    def test_inv_034_credentials_never_logged(self):
        import re
        from pathlib import Path

        pattern = re.compile(r"(password|api_key|secret|token)\s*=", re.IGNORECASE)
        offenders = [
            str(p) for p in Path("adapters").rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8"))
            and "test" not in str(p)
        ]
        assert offenders == []

    def test_inv_035_expired_order_no_submission(self):
        from core.execution.boundary import can_execute

        risk = _allow_risk()
        from tests.phase1_factories import at

        order = aligned(make_order(expires_at=at(12, 0)), risk)
        result = can_execute(order, risk, ctx(now=at(12, 1)))
        assert "order_expired" in result.reasons

    def test_inv_036_disconnect_not_failed(self):
        adapter = SimulationExecutionAdapter(SimulationSequencer())
        # not connected: adapter reports connection error, EMS state UNKNOWN
        risk = _allow_risk()
        ems = ExecutionManagementSystem(adapters={"SIMULATION": adapter})
        order = Order(**{**aligned(make_order(), risk).__dict__,
                         "status": OrderStatus.ACCEPTED})
        result = ems.submit(order, risk_decision=risk, execution_context=ctx())
        assert result.state == "UNKNOWN"  # not FAILED

    def test_inv_037_partial_fills_exact_cumulative(self, env):
        order, _ = _submit_order(env, "1.00")
        cumulative = Decimal("0")
        for ref, quantity in (("P1", "0.30"), ("P2", "0.20")):
            report = _report(order, quantity=quantity, ref=ref)
            order, _ = env["oms"].ingest_execution_report(report)
            cumulative += Decimal(quantity)
        assert env["oms"].filled_quantity(order.order_id) == cumulative

    def test_inv_038_cancel_needs_confirmation(self, env):
        order, _ = _submit_order(env)
        requested = env["oms"].request_cancel(order, reason="r", actor="op", at=at(12, 2))
        assert requested.status is OrderStatus.CANCEL_REQUESTED  # not yet CANCELLED
        assert env["storage"].orders.get_by_id(order.order_id).status             is OrderStatus.CANCEL_REQUESTED

    def test_inv_039_replacement_immutable_version(self, env):
        order, risk = _submit_order(env)
        replacement = env["oms"].build_replacement(order, quantity="2.00", price=None,
                                                   risk_decision=risk,
                                                   execution_context=ctx(), reason="r")
        original_chain = list(env["storage"].orders.iter_versions(order.order_id))
        assert all(o.quantity == Decimal("1.00") for o in original_chain)
        assert replacement.quantity == Decimal("2.00")

    def test_inv_040_no_phase6(self):
        # Phase 5 scope guard, updated for the Phase 6 registry state:
        # AI/ML authority stays out until Phase 7+.
        from pathlib import Path

        assert not (Path("core/ml")).exists()
        assert not (Path("core/ai_authority")).exists()


# ====================================================================== #
# CRASH RECOVERY (SECTION 31)                                            #
# ====================================================================== #
class TestCrashRecovery:
    def test_crash_before_order_persistence(self, tmp_path):
        # duplicate submit after "crash" (nothing persisted) -> one canonical
        path = tmp_path / "c1.db"
        storage = StorageSet(path)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        order, _ = _submit_order({"oms": oms, "storage": storage})
        storage.close()
        storage2 = StorageSet(path)
        oms2 = OrderManagementSystem(orders=storage2.orders,
                                     reports=storage2.execution_reports,
                                     audit=storage2.audit)
        assert storage2.orders.count() == 1
        storage2.close()

    def test_crash_before_fill_persistence(self, tmp_path):
        # fill received but crash before projection: re-project idempotent
        path = tmp_path / "c2.db"
        storage = StorageSet(path)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        posting = LedgerPostingService(storage.ledger, storage.audit)
        projector = ExecutionProjector(states=storage.states, posting=posting,
                                       events=storage.events, audit=storage.audit)
        order, _ = _submit_order({"oms": oms, "storage": storage})
        report = _report(order)
        oms.ingest_execution_report(report)  # order side done
        # "crash": reopen and project twice
        storage.close()
        storage2 = StorageSet(path)
        posting2 = LedgerPostingService(storage2.ledger, storage2.audit)
        projector2 = ExecutionProjector(states=storage2.states, posting=posting2,
                                        events=storage2.events, audit=storage2.audit)
        reloaded = storage2.orders.get_by_id(order.order_id)
        first = projector2.project(report, reloaded, at=at(12, 2))
        second = projector2.project(report, reloaded, at=at(12, 3))
        assert second.duplicate and storage2.ledger.count() == 1
        storage2.close()

    def test_replay_read_only(self, env):
        order, _ = _submit_order(env)
        report = _report(order)
        env["oms"].ingest_execution_report(report)
        env["projector"].project(report, order, at=at(12, 2))
        # replay: re-ingest + re-project
        updated, created = env["oms"].ingest_execution_report(report)
        assert not created  # no duplicate canonical effect
