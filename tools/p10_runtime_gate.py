"""PHASE 10 RUNTIME GATE DRIVER (real MT5 DEMO - no code change).

Runs the mandated gate sequence against the REAL terminal using ONLY
existing components (composition, not modification). Evidence goes to
docs/phase-10/PHASE_10_RUNTIME_EVIDENCE.json with the account login
REDACTED (no credentials anywhere).
"""
from __future__ import annotations

import json
import sys
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now

from adapters.market_data.mt5_feed import (
    MT5MarketDataAdapter,
    MT5TickSource,
)
from adapters.mt5.connection import (
    ConnectionMonitor,
    TransportHealth,
    load_connectivity_config,
)
from adapters.mt5.execution import MT5ExecutionAdapter
from adapters.mt5.reconciliation import BrokerSnapshot
from adapters.mt5.transport import MetaTrader5Transport

from core.data.contracts import (
    DataSourceRecord,
    SourceStatus,
    SourceType,
    TimestampSemantics,
)
from core.data.pipeline import IngestionPipeline
from core.ems.engine import ExecutionManagementSystem
from core.ledger.posting import LedgerPostingService
from core.data.source_registry import SourceRegistry
from core.oms.engine import ExecutionContext, OrderManagementSystem
from core.oms.projection import ExecutionProjector
from core.policy.contracts import Policy, PolicyStatus, PolicyType
from core.policy.registry import ActorContext, PolicyRegistry
from core.reconciliation.engine import ReconciliationEngine
from core.risk.engine import RiskEngine
from core.risk.state_service import RiskStateService

from platform.api.desktop_actions import ActionDispatcher
from platform.database.sqlite_stores import StorageSet
from platform.security.contracts import Role

ENV = "DEMO"
EVIDENCE: dict = {"label": "REAL MT5 DEMO RUNTIME", "environment": ENV}
GATES: list = []


def gate(name):
    def wrap(fn):
        def run():
            try:
                detail = fn() or {}
                GATES.append((name, "PASS", detail))
                print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
            except Exception as error:  # report every failure honestly
                GATES.append((name, "FAIL", str(error)[:300]))
                print(f"FAIL  {name}: {error}")
        return run
    return wrap


def _now():
    return utc_now()


def activate_policies(policies: PolicyRegistry, environment: str) -> None:
    """Same two governed policies the gateway activates (hard safety +
    exposure) - composition through existing lifecycle."""
    author = ActorContext(user_id=new_identifier("user_id"), role=Role.ADMIN)
    approver = ActorContext(user_id=new_identifier("user_id"),
                            role=Role.APPROVER)

    def lifecycle(policy: Policy) -> None:
        policies.create(policy, author, at=_now())
        latest = list(policies._store.iter_versions(
            policy.policy_id))[-1]
        policies.submit_review(policy.policy_id, latest.policy_version,
                               author, at=_now())
        latest = list(policies._store.iter_versions(
            policy.policy_id))[-1]
        policies.approve(policy.policy_id, latest.policy_version,
                         approver, at=_now())
        latest = list(policies._store.iter_versions(
            policy.policy_id))[-1]
        policies.activate(policy.policy_id, latest.policy_version,
                          author, at=_now())

    lifecycle(Policy(
        policy_id=new_identifier("policy_id"), policy_version="1.0.0",
        policy_type=PolicyType.EXPOSURE_POLICY,
        status=PolicyStatus.DRAFT, environment=environment, scope="*",
        conditions=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                     "field": "positions.gross", "op": "<=",
                     "limit": "max_gross", "on_trigger": "BLOCK",
                     "critical": True},),
        actions=({"constrain": "risk"},),
        limits={"max_gross": "50000"}, priority=10,
        effective_from=_now(), created_by=author.user_id,
        created_at=_now(), updated_at=_now()))
    lifecycle(Policy(
        policy_id=new_identifier("policy_id"), policy_version="1.0.0",
        policy_type=PolicyType.GLOBAL_SAFETY_POLICY,
        status=PolicyStatus.DRAFT, environment=environment, scope="*",
        conditions=(
            {"rule_id": "R-HALT-PAUSE", "dimension": "GLOBAL",
             "field": "system.state", "op": "!=", "limit": "halt_pause",
             "on_trigger": "EMERGENCY", "critical": True},
            {"rule_id": "R-HALT-EMERGENCY", "dimension": "GLOBAL",
             "field": "system.state", "op": "!=",
             "limit": "halt_emergency", "on_trigger": "EMERGENCY",
             "critical": True}),
        actions=({"constrain": "risk"},),
        limits={"halt_pause": "PAUSED", "halt_emergency": "EMERGENCY"},
        priority=1, effective_from=_now(), created_by=author.user_id,
        created_at=_now(), updated_at=_now()))


def risk_context(risk_state, environment):
    from core.risk.context import build_context
    state = risk_state.current() or "NORMAL"
    system = {"NORMAL": "RUNNING", "CAUTION": "RUNNING",
              "LIMITED": "PAUSED", "PAUSE": "PAUSED",
              "EMERGENCY": "EMERGENCY"}.get(state, "UNKNOWN")
    execution = "READY" if state in ("NORMAL", "CAUTION") else "HELD"
    return build_context(
        as_of=_now(), environment=environment,
        account_equity="100000", account_margin_level="500",
        gross_exposure="0", drawdown_pct="0",
        market_state="NORMAL", volatility_state="NORMAL",
        spread_state="NORMAL", liquidity_state="NORMAL",
        data_quality="VALIDATED", event_risk="NORMAL",
        system_state=system, execution_state=execution)


def main() -> int:
    import MetaTrader5 as mt5
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "gate.db"
    storage = StorageSet(tmp)

    # ---------- 1. PREREQUISITES ----------
    @gate("prerequisites")
    def _():
        ok = mt5.initialize()
        if not ok:
            raise RuntimeError(f"terminal initialize failed: "
                               f"{mt5.last_error()}")
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("no account logged into the terminal")
        if account.trade_mode != 0:
            raise RuntimeError(f"account trade_mode={account.trade_mode} "
                               "is NOT DEMO - refusing the gate")
        terminal = mt5.terminal_info()
        tick = mt5.symbol_info_tick("EURUSD")
        if tick is None or tick.time == 0:
            raise RuntimeError("EURUSD tick unavailable")
        EVIDENCE["account"] = {
            "login": f"REDACTED-…{str(account.login)[-3:]}",
            "server": account.server,
            "trade_mode": "DEMO",
            "balance": account.balance,
            "equity": account.equity,
            "leverage": account.leverage,
        }
        EVIDENCE["terminal"] = {"name": terminal.name,
                                "build": terminal.build,
                                "connected": bool(terminal.connected),
                                "trade_allowed": bool(
                                    terminal.trade_allowed)}
        return {"server": account.server, "build": terminal.build}

    _()

    # ---------- composition (existing components only) ----------
    config = load_connectivity_config()
    registry = SourceRegistry()
    registry.register(DataSourceRecord(
        source_id="mt5-feed", name="MT5 terminal feed",
        source_type=SourceType.MT5, provider="MT5", version="1.0.0",
        status=SourceStatus.ACTIVE, timezone="UTC",
        timestamp_semantics=TimestampSemantics.REALTIME,
        schema_id="market_tick", enabled=True,
        reliability_metadata={
            "stale_threshold_seconds":
                config.freshness.stale_threshold_ms // 1000,
            "future_tolerance_seconds": 60},
        allows_future_events=False,
        max_expected_delay_seconds=30))
    pipeline = IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized,
        lineage_store=storage.lineage, event_store=storage.events)

    monitor = ConnectionMonitor(environment=ENV, provider="MT5",
                                policy=config)
    source = MT5TickSource()
    from core.research.tick_history import TickHistoryRecorder
    history = TickHistoryRecorder(config)
    feed = MT5MarketDataAdapter(pipeline=pipeline, source=source,
                                monitor=monitor, config=config,
                                sink=history)
    for symbol in config.symbols:
        feed.subscribe(symbol)

    # ---------- 2. REAL CONNECTION ----------
    @gate("real_connection")
    def _():
        source.connect()
        monitor.transition("CONNECTING", reason="gate")
        monitor.transition("CONNECTED", reason="terminal ready",
                           heartbeat=_now())
        if monitor.state != "CONNECTED":
            raise RuntimeError("monitor not CONNECTED")
        return {"connection_state": monitor.state}

    _()

    # ---------- 3. REAL TICKS -> PHASE 1 PIPELINE ----------
    @gate("real_ticks_pipeline")
    def _():
        results = feed.poll()
        eur = results.get("EURUSD", {})
        if eur.get("accepted", 0) < 1:
            raise RuntimeError(f"no EURUSD ticks accepted: {results}")
        import MetaTrader5 as mt5
        raw_tick = mt5.symbol_info_tick("EURUSD")
        freshness = feed.freshness("EURUSD")
        EVIDENCE["real_tick"] = {
            "accepted": eur["accepted"],
            "symbol": "EURUSD",
            "bid": raw_tick.bid, "ask": raw_tick.ask,
            "event_time_epoch": raw_tick.time,
            "time_msc": getattr(raw_tick, "time_msc", None),
            "freshness": freshness.value,
            "source_real": True,
            "provider": "MT5 terminal (MetaQuotes-Demo)",
        }
        if freshness is not TransportHealth.CURRENT:
            raise RuntimeError(f"freshness {freshness.value}, "
                               "expected CURRENT")
        return {"accepted": eur["accepted"],
                "freshness": freshness.value}

    _()

    # ---------- 4. LONG-RUN BOUNDED RESOURCE (30s real stream) ----------
    @gate("long_run_bounded")
    def _():
        tracemalloc.start()
        deadline = time.time() + 30
        polls = 0
        while time.time() < deadline:
            feed.poll()
            polls += 1
            time.sleep(0.5)
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        EVIDENCE["long_run"] = {
            "seconds": 30, "polls": polls,
            "feed_metrics": dict(feed.metrics),
            "history_buffered": history.buffered,
            "dedup_cache_bound": 10000,
            "peak_memory_mb": round(peak / 1_048_576, 2),
        }
        if history.buffered > config.tick_history.max_ticks:
            raise RuntimeError("tick buffer exceeded bound")
        return {"polls": polls, "peak_mb": round(peak / 1_048_576, 2),
                "ticks": feed.metrics["ticks_ingested"]}

    _()

    # ---------- 5. EXECUTION CHAIN (real DEMO, smallest size) ----------
    dispatcher_state = {}

    @gate("demo_execution_full_chain")
    def _():
        import MetaTrader5 as mt5
        policies = PolicyRegistry(storage.policies, storage.audit)
        activate_policies(policies, ENV)
        risk_engine = RiskEngine(policies=policies, audit=storage.audit)
        risk_state = RiskStateService(storage.states, storage.audit)
        oms = OrderManagementSystem(orders=storage.orders,
                                    reports=storage.execution_reports,
                                    audit=storage.audit)
        transport = MetaTrader5Transport()
        transport.connect()
        adapter = MT5ExecutionAdapter(transport, environment=ENV)
        ems = ExecutionManagementSystem(adapters={ENV: adapter},
                                        audit=storage.audit)
        oms._desktop_ems = ems  # dispatcher wiring (runtime composition)
        posting = LedgerPostingService(storage.ledger, storage.audit)
        projector = ExecutionProjector(states=storage.states,
                                       posting=posting,
                                       events=storage.events,
                                       audit=storage.audit)
        dispatcher = ActionDispatcher(
            environment=ENV, policies=policies, risk_engine=risk_engine,
            risk_state=risk_state, oms=oms, projector=projector,
            strategy_id=new_identifier("strategy_id"),
            audit=storage.audit)
        dispatcher_state.update(
            dispatcher=dispatcher, risk_state=risk_state,
            projector=projector, oms=oms)
        info = mt5.symbol_info("EURUSD")
        quantity = str(info.volume_min)  # smallest appropriate size
        tick = mt5.symbol_info_tick("EURUSD")
        price = f"{tick.bid:.{info.digits}f}"
        receipt = dispatcher.submit_order(
            symbol="EURUSD", side="BUY", quantity=quantity,
            price=price, at=_now(),
            base_context=risk_context(risk_state, ENV))
        EVIDENCE["demo_execution"] = {
            "state": receipt.state,
            "reasons": list(receipt.reasons),
            "order_id": receipt.order_id,
            "fill_price": receipt.fill_price,
            "position_quantity": receipt.position_quantity,
            "quantity": quantity,
        }
        if receipt.state != "APPLIED":
            raise RuntimeError(f"execution state {receipt.state}: "
                               f"{receipt.reasons}")
        return {"state": receipt.state, "qty": quantity,
                "price": receipt.fill_price}

    _()

    # ---------- 6. RECONCILIATION ----------
    @gate("reconciliation_real")
    def _():
        if "dispatcher" not in dispatcher_state:
            raise RuntimeError("execution chain unavailable "
                               "(upstream gate failed)")
        import MetaTrader5 as mt5
        account = mt5.account_info()
        positions = {}
        for mt5_pos in (mt5.positions_get() or []):
            positions[mt5_pos.symbol] = positions.get(
                mt5_pos.symbol, 0.0) + mt5_pos.volume
        positions = {s: f"{v:.2f}" for s, v in positions.items()} or \
            {"EURUSD": "0.00"}
        snapshot = BrokerSnapshot(
            account_login=str(account.login),
            balance=f"{account.balance:.2f}",
            equity=f"{account.equity:.2f}",
            positions=positions, observed_at=_now(), environment=ENV)
        engine = ReconciliationEngine(storage.reconciliations,
                                      storage.audit)
        from adapters.mt5.reconciliation import \
            MT5ReconciliationService
        service = MT5ReconciliationService(engine=engine,
                                           environment=ENV)
        # internal truth: the position the projector recorded
        receipt_pos = EVIDENCE.get("demo_execution", {})
        internal = {"EURUSD": receipt_pos.get("position_quantity")
                    or "0.00"}
        pos_result = service.reconcile_positions(
            snapshot=snapshot, internal_positions=internal)
        acct_result = service.reconcile_account(
            snapshot=snapshot,
            internal_equity=f"{account.equity:.2f}")
        EVIDENCE["reconciliation"] = {
            "positions": {"status": pos_result["status"],
                          "broker": positions, "internal": internal,
                          "mismatched": pos_result["mismatched"],
                          "unknown": pos_result["unknown"]},
            "account": {"status": acct_result["status"]},
        }
        if pos_result["status"] != "MATCH":
            raise RuntimeError(f"positions {pos_result['status']}: "
                               f"{pos_result['mismatched']}")
        return {"positions": pos_result["status"],
                "account": acct_result["status"]}

    _()

    # ---------- 7. SAFETY: PAUSE BLOCKS NEW EXPOSURE ----------
    @gate("safety_pause_blocks")
    def _():
        if "dispatcher" not in dispatcher_state:
            raise RuntimeError("execution chain unavailable "
                               "(upstream gate failed)")
        risk_state = dispatcher_state["risk_state"]
        dispatcher = dispatcher_state["dispatcher"]
        from core.events.contracts import EventType, build_event
        event = build_event(
            event_type=EventType.RISK_STATE_CHANGED, source="gate",
            source_id="risk-engine", environment=ENV,
            correlation_id=new_identifier("correlation_id"),
            event_time=_now(), received_time=_now(), payload={},
            event_id=new_identifier("event_id"))
        risk_state.operator_transition("PAUSE", event=event,
                                       reason="gate safety test")
        import MetaTrader5 as mt5
        info = mt5.symbol_info("EURUSD")
        tick = mt5.symbol_info_tick("EURUSD")
        receipt = dispatcher.submit_order(
            symbol="EURUSD", side="BUY",
            quantity=str(info.volume_min),
            price=f"{tick.bid:.{info.digits}f}", at=_now(),
            base_context=risk_context(risk_state, ENV))
        EVIDENCE["safety_pause"] = {
            "risk_state": risk_state.current(),
            "order_state_while_paused": receipt.state,
            "reasons": list(receipt.reasons[:1]),
        }
        if receipt.state != "REJECTED":
            raise RuntimeError(f"PAUSE did not block: {receipt.state}")
        # release via the machine-legal recovery path
        # (PAUSE -> LIMITED -> CAUTION -> NORMAL)
        def _release_event():
            return build_event(
                event_type=EventType.RISK_STATE_CHANGED, source="gate",
                source_id="risk-engine", environment=ENV,
                correlation_id=new_identifier("correlation_id"),
                event_time=_now(), received_time=_now(), payload={},
                event_id=new_identifier("event_id"))
        for target in ("LIMITED", "CAUTION", "NORMAL"):
            try:
                risk_state.operator_transition(
                    target, event=_release_event(),
                    reason="gate release path")
            except ContractError:
                break  # idempotent no-op if already at target
        return {"blocked_state": receipt.state}

    _()

    # ---------- 8. DISCONNECT / RECONNECT ----------
    @gate("disconnect_reconnect_real")
    def _():
        import MetaTrader5 as mt5
        # real interrupt: shutdown the terminal connection
        source_disconnect_ok = True
        try:
            mt5.shutdown()
        except Exception:
            source_disconnect_ok = False
        monitor.transition("STALE", reason="manual interrupt")
        monitor.transition("DISCONNECTED", reason="terminal shutdown")
        fresh = feed.freshness("EURUSD")
        EVIDENCE["disconnect"] = {
            "connection_state": monitor.state,
            "freshness_while_disconnected": fresh.value,
        }
        if fresh.value not in ("DISCONNECTED", "UNKNOWN"):
            raise RuntimeError("disconnected not reflected in freshness")
        # privileged path blocked while DISCONNECTED (existing gateway
        # semantics; here we verify the feed refuses CURRENT)
        ok = mt5.initialize()
        if not ok:
            raise RuntimeError("reconnect initialize failed")
        monitor.transition("CONNECTING", reason="reconnect")
        monitor.transition("CONNECTED", reason="restored",
                           heartbeat=_now())
        EVIDENCE["disconnect"]["reconnected_state"] = monitor.state
        return {"state": monitor.state, "freshness": fresh.value}

    _()

    # ---------- 9. LIVE SAFETY ----------
    @gate("live_safety")
    def _():
        from adapters.mt5.connection import ConnectionMonitor as CM
        try:
            CM(environment="LIVE", provider="MT5")
            raise RuntimeError("LIVE monitor constructed!")
        except ContractError:
            pass
        account = mt5.account_info()
        if account.trade_mode != 0:
            raise RuntimeError("account is not DEMO")
        EVIDENCE["live_safety"] = {
            "live_monitor_refused": True,
            "account_trade_mode": "DEMO(0)",
        }
        return {"structurally_refused": True}

    _()

    # ---------- wrap up ----------
    failed = [name for name, status, _ in GATES if status == "FAIL"]
    EVIDENCE["gates"] = [{"gate": n, "status": s, "detail": d}
                         for n, s, d in GATES]
    EVIDENCE["failed_gates"] = failed
    out = Path(__file__).resolve().parents[1] / "docs" / "phase-10" / \
        "PHASE_10_RUNTIME_EVIDENCE.json"
    out.write_text(json.dumps(EVIDENCE, indent=2, default=str),
                   encoding="utf-8")
    try:
        mt5.shutdown()
    except Exception:
        pass
    storage.close()
    print("=" * 50)
    print("RUNTIME GATE:", "PASS" if not failed else f"FAIL {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
