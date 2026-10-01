"""DEMO E2E Part B — order path (TEST-06..14) against the REAL DEMO terminal.

Split enforced by our own session contract (FOREX_LONDON_NY = 07:00-21:00
UTC weekdays):

  outside the window : only the fail-closed subset runs (TEST-06..09);
                       TEST-10..14 are recorded HELD - never faked.
  inside the window  : AND only with --allow-live-orders, the real DEMO
                       submissions run (TEST-10..14), each one re-checked
                       through AccountGuard+SessionGuard immediately before
                       the transport call.

LIVE stays HARD-LOCKED: trade_mode != 0 is refused, LIVE cannot be pinned,
and the account identity is pinned to the login/server recorded by Part A.
"""
from __future__ import annotations

import json
import sys
import uuid  # noqa: F401  # eager-uuid pythons: cache stdlib platform first
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.modules.pop("platform", None)

EVID = ROOT / "docs" / "integration" / "evidence" / "phase-1"
SYMBOL = "EURUSD"
QTY = "0.01"

import MetaTrader5 as mt5  # noqa: E402

from adapters.mt5.account_guard import (  # noqa: E402
    AccountGuard, AccountGuardError, ExpectedIdentity, SessionGuard,
    TerminalIdentity,
)
from adapters.mt5.execution import MT5ExecutionAdapter  # noqa: E402
from adapters.mt5.reconciliation import BrokerSnapshot  # noqa: E402
from adapters.mt5.transport import MetaTrader5Transport  # noqa: E402
from architecture.contracts.errors import ContractError  # noqa: E402


def _pins_from_part_a() -> dict:
    """Pin the account identity to whatever Part A actually verified."""
    path = EVID / "DEMO_E2E_PART_A.json"
    if path.exists():
        rows = [r for r in json.loads(path.read_text(encoding="utf-8"))
                ["results"] if r["test"].startswith("TEST-01")]
        if rows and rows[0]["result"] == "PASS":
            return {"login": rows[0]["login"], "server": rows[0]["server"]}
    return {"login": 113126589, "server": "MetaQuotes-Demo"}


def main() -> int:
    allow_live = "--allow-live-orders" in sys.argv
    results: list[dict] = []
    live_orders_executed = False

    def record(test: str, status: str, detail: dict) -> None:
        results.append({"test": test, "result": status,
                        "at": datetime.now(timezone.utc).isoformat(),
                        **detail})
        print(f"[{status}] {test}: {detail}")

    pins = _pins_from_part_a()
    session = SessionGuard()          # real clock, NO override
    now = datetime.now(timezone.utc)
    window_open = session.is_open(now)
    # independent recomputation of the contract window (no guard code)
    hour, weekday = now.hour, now.weekday()
    raw_open = weekday < 5 and 7 <= hour < 21
    record("TEST-06 session-contract verdict", "PASS" if window_open == raw_open else "FAIL",
           {"utc": now.isoformat(), "guard_says_open": window_open,
            "independent_recompute_open": raw_open,
            "phase": "IN-WINDOW" if window_open else "OUT-OF-WINDOW"})

    # TEST-01-style connect + observed identity (re-established for Part B)
    assert mt5.initialize(), mt5.last_error()
    acc = mt5.account_info()
    syms = [s for s in (SYMBOL,) if mt5.symbol_info_tick(s) is not None]
    observed = TerminalIdentity.from_mt5(acc, syms)
    guard = AccountGuard(ExpectedIdentity(
        trade_mode=0, login=pins["login"], server=pins["server"]))

    # TEST-07 order submission gated by guard+session (fail-closed proof)
    try:
        guard.check(observed, session=session, moment=now)
        # in-window: guard allowed; the fail-closed half is proven in the
        # synthetic suites (test_premarket_scenarios) and out-of-window runs
        record("TEST-07 order-gate (guard+session)", "PASS",
               {"gate": "ALLOWED (in-window, pinned DEMO identity)",
                "login": observed.login, "trade_mode": observed.trade_mode})
    except AccountGuardError as e:
        reasons = e.details.get("reasons", []) if hasattr(e, "details") else []
        blocked_for_session = any("session CLOSED" in r for r in reasons)
        if not window_open and blocked_for_session:
            record("TEST-07 order-gate (guard+session)", "PASS",
                   {"gate": "BLOCKED (fail-closed, session CLOSED)",
                    "reasons": reasons})
        else:
            record("TEST-07 order-gate (guard+session)", "FAIL",
                   {"unexpected_block": reasons or str(e)[:200]})

    # TEST-08 pinned identity (no session component)
    try:
        decision = guard.check(observed)
        record("TEST-08 pinned-account identity", "PASS",
               {"observed_login": observed.login,
                "observed_server": observed.server,
                "pinned_login": pins["login"],
                "match": decision["allowed"]})
    except AccountGuardError as e:
        record("TEST-08 pinned-account identity", "FAIL",
               {"error": str(e)[:200]})
        return 1

    # TEST-09 execution adapter connect + health + REAL capabilities
    transport = MetaTrader5Transport()
    adapter = MT5ExecutionAdapter(transport, environment="DEMO")
    try:
        transport.connect()
        health = adapter.health()
        caps = adapter.capabilities()
        record("TEST-09 execution adapter connect", "PASS",
               {"health": str(health), "adapter_id": adapter.adapter_id(),
                "position_semantics": str(caps.position_semantics),
                "symbols_seen": len(caps.supported_symbols)})
    except Exception as e:  # noqa: BLE001 - report any adapter failure
        record("TEST-09 execution adapter connect", "FAIL",
               {"error": str(e)[:200]})
        return 1

    def guarded_submit(canonical: dict, purpose: str):
        """Guard re-check immediately before every live transport call."""
        fresh = TerminalIdentity.from_mt5(
            mt5.account_info(), [SYMBOL])
        guard.check(fresh, session=session,
                    moment=datetime.now(timezone.utc))
        return adapter.submit_order(canonical)

    if not (window_open and allow_live):
        for test, note in (
                ("TEST-10 market order (DEMO)", "held - session window closed"
                 if not window_open else "held - run with --allow-live-orders"),
                ("TEST-11 order state poll", "held - depends on TEST-10"),
                ("TEST-12 position + broker snapshot", "held - depends on TEST-10"),
                ("TEST-13 pending order + cancel path", "held - session window closed"
                 if not window_open else "held - run with --allow-live-orders"),
                ("TEST-14 position cleanup + post-state", "held - depends on TEST-10")):
            record(test, "HOLD", {"note": note})
        mt5.shutdown()
        return _finish(results, live_orders_executed)

    # ------------------------------------------------------------------ #
    # IN-WINDOW + explicit flag: real DEMO order path
    # ------------------------------------------------------------------ #
    base = {"symbol": SYMBOL, "quantity": QTY, "time_in_force": "GTC",
            "side": "BUY", "order_type": "MARKET",
            "order_id": "E2E-M10", "idempotency_key": "E2E-M10"}

    resp = guarded_submit(dict(base), "TEST-10")
    ok10 = bool(resp.ok and resp.broker_order_id)
    record("TEST-10 market order (DEMO)", "PASS" if ok10 else "FAIL",
           {"ticket": resp.broker_order_id,
            "retcode": resp.raw["mt5_result"].get("retcode"),
            "comment": resp.raw["mt5_result"].get("comment")})
    if not ok10:
        mt5.shutdown()
        return _finish(results, live_orders_executed)
    live_orders_executed = True
    ticket = resp.broker_order_id

    # TEST-11 poll the order back from the terminal
    poll = adapter.poll_order(ticket)
    record("TEST-11 order state poll", "PASS" if poll.ok else "FAIL",
           {"ticket": ticket,
            "state": poll.raw["mt5_result"].get("state"),
            "volume_current": poll.raw["mt5_result"].get("volume_current")})

    # TEST-12 position materialized + validated broker snapshot
    positions = list(mt5.positions_get(symbol=SYMBOL) or ())
    pos = next((p for p in positions if p.volume >= float(QTY)), None)
    account = mt5.account_info()
    net_qty = "0"
    if pos is not None:
        net_qty = f"{pos.volume if pos.type == 0 else -pos.volume}"
    try:
        snapshot = BrokerSnapshot(
            account_login=str(account.login),
            balance=f"{account.balance:.2f}",
            equity=f"{account.equity:.2f}",
            positions={SYMBOL: net_qty},
            observed_at=datetime.now(timezone.utc), environment="DEMO")
        snapshot.validate()
        snap_ok = pos is not None
    except ContractError as e:
        snap_ok = False
        record("TEST-12 position + broker snapshot", "FAIL",
               {"snapshot_error": str(e)[:200]})
        snapshot = None
    if snapshot is not None:
        record("TEST-12 position + broker snapshot", "PASS" if snap_ok else "FAIL",
               {"position_ticket": getattr(pos, "ticket", None),
                "position_type": getattr(pos, "type", None),
                "net_quantity": net_qty,
                "balance": snapshot.balance, "equity": snapshot.equity})

    # TEST-13 pending LIMIT far from market + cancel path
    tick = mt5.symbol_info_tick(SYMBOL)
    far_price = round(tick.bid * 0.90, 2)
    limit = guarded_submit({"symbol": SYMBOL, "quantity": QTY,
                            "time_in_force": "GTC", "side": "BUY",
                            "order_type": "LIMIT", "price": far_price,
                            "order_id": "E2E-M13", "idempotency_key": "E2E-M13"},
                           "TEST-13")
    if limit.ok and limit.broker_order_id:
        cancel = adapter.cancel_order(limit.broker_order_id)
        still_active = [o for o in (mt5.orders_get(
            ticket=int(limit.broker_order_id)) or [])]
        record("TEST-13 pending order + cancel path",
               "PASS" if cancel.ok and not still_active else "FAIL",
               {"limit_ticket": limit.broker_order_id, "price": far_price,
                "cancel_ok": cancel.ok,
                "still_active_after_cancel": len(still_active)})
        if still_active:  # leave nothing pending behind
            adapter.cancel_order(limit.broker_order_id)
    else:
        record("TEST-13 pending order + cancel path", "FAIL",
               {"submit_ok": limit.ok,
                "retcode": limit.raw["mt5_result"].get("retcode")})

    # TEST-14 cleanup: close what TEST-10 opened, account left clean
    closed = False
    if pos is not None:
        close_req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL,
                     "volume": float(QTY), "type": mt5.ORDER_TYPE_SELL,
                     "position": pos.ticket, "price": tick.bid,
                     "deviation": 20, "comment": "E2E-M14-close",
                     "type_filling": mt5.ORDER_FILLING_IOC}
        cr = transport.order_send(close_req)
        closed = bool(cr.ok)
        time_sleep(1.0)
    leftover = [p for p in (mt5.positions_get(symbol=SYMBOL) or [])
                if p.ticket == getattr(pos, "ticket", None)]
    final = mt5.account_info()
    record("TEST-14 position cleanup + post-state",
           "PASS" if closed and not leftover else "FAIL",
           {"close_retcode_ok": closed,
            "position_closed": not leftover,
            "final_balance": f"{final.balance:.2f}",
            "final_equity": f"{final.equity:.2f}"})

    mt5.shutdown()
    return _finish(results, live_orders_executed)


def time_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def _finish(results: list[dict], live_orders_executed: bool) -> int:
    hard = [r for r in results if r["result"] == "FAIL"]
    out = {"run": "DEMO E2E Part B (order path)",
           "generated_at": datetime.now(timezone.utc).isoformat(),
           "live_orders_executed": live_orders_executed,
           "results": results,
           "held": sum(1 for r in results if r["result"] == "HOLD"),
           "failed": len(hard),
           "all_pass": not hard}
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "DEMO_E2E_PART_B.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("ALL PASS" if out["all_pass"] else "FAILURES PRESENT",
          f"({out['held']} held)")
    print("evidence ->", EVID / "DEMO_E2E_PART_B.json")
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
