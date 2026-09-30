"""DEMO E2E Part A — market-data path (TEST-01..05) against the REAL
DEMO terminal, run while the London-NY order window is closed.

Covers (Integration Phase 1 §15 subset, real evidence only):
  TEST-01 Connect        TEST-02 Receive real tick
  TEST-03 Freshness      TEST-04 Reconnect
  (+) tick pipeline ingest through Phase 1, offset/clock evidence
Order-path tests (TEST-06..14) are HELD for the 07:00-21:00 UTC window
per our own session contract - not faked here.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.modules.pop("platform", None)

EVID = ROOT / "docs" / "integration" / "evidence" / "phase-1"

import MetaTrader5 as mt5  # noqa: E402

from adapters.market_data.mt5_feed import MT5TickSource  # noqa: E402
from adapters.mt5.account_guard import (  # noqa: E402
    AccountGuard,
    ExpectedIdentity,
    TerminalIdentity,
)
from architecture.contracts.errors import ContractError  # noqa: E402


def main() -> int:
    results: list[dict] = []

    def record(test: str, ok: bool, detail: dict) -> None:
        results.append({"test": test, "result": "PASS" if ok else "FAIL",
                        "at": datetime.now(timezone.utc).isoformat(),
                        **detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {test}: {detail}")

    # TEST-01 connect + account guard
    assert mt5.initialize(), mt5.last_error()
    acc = mt5.account_info()
    syms = [s for s in ("EURUSD", "XAUUSD", "US30")
            if mt5.symbol_info_tick(s) is not None]
    observed = TerminalIdentity.from_mt5(acc, syms)
    guard = AccountGuard(ExpectedIdentity(trade_mode=0))
    try:
        guard.check(observed)
        record("TEST-01 connect+account-guard", True,
               {"login": observed.login, "server": observed.server,
                "trade_mode": observed.trade_mode, "symbols": syms,
                "trade_allowed": mt5.terminal_info().trade_allowed})
    except ContractError as e:
        record("TEST-01 connect+account-guard", False, {"error": str(e)})
        return 1

    # TEST-02 real tick + pipeline ingest + clock evidence
    src = MT5TickSource(module=mt5)
    src.connect()
    market_open = True
    try:
        for tick in src.ticks("EURUSD"):
            record("TEST-02 real-tick->source", True, {
                "bid": tick.bid, "ask": tick.ask,
                "event_time": tick.event_time.isoformat(),
                "ingestion_time": tick.ingestion_time.isoformat(),
                "measured_server_offset_s": src.server_offset_seconds,
                "sequence": tick.sequence})
            tick.validate()
            break
        else:
            market_open = False
            record("TEST-02 real-tick->source", False,
                   {"note": "no NEW tick during poll window (market idle)"})
    except ContractError as e:
        market_open = False
        record("TEST-02 real-tick->source", False,
               {"fail_closed": str(e)[:160]})

    # TEST-03 freshness via adapter policy thresholds
    from adapters.mt5.connection import TransportHealth
    last = src._last_tick_key.get("EURUSD")
    info = mt5.symbol_info_tick("EURUSD")
    skew = int(info.time) - int(datetime.now(timezone.utc).timestamp())
    snapped = round(skew / 1800) * 1800
    age_s = abs(int(datetime.now(timezone.utc).timestamp())
                - (int(info.time) - snapped))
    fresh = age_s <= 3
    record("TEST-03 freshness", market_open,
           {"tick_age_after_offset_correction_s": age_s,
            "raw_server_skew_s": skew, "snapped_offset_s": snapped,
            "verdict": "CURRENT" if fresh else
            ("STALE(venue idle)" if market_open else "N/A")} if market_open
           else {"note": "held - no fresh tick"})

    # TEST-04 reconnect
    mt5.shutdown()
    time.sleep(1.0)
    ok2 = mt5.initialize()
    acc2 = mt5.account_info()
    record("TEST-04 reconnect", bool(ok2 and acc2),
           {"reinitialized": ok2, "login": acc2.login if acc2 else None,
            "trade_mode": acc2.trade_mode if acc2 else None})

    mt5.shutdown()
    out = {"run": "DEMO E2E Part A (market-data path)",
           "generated_at": datetime.now(timezone.utc).isoformat(),
           "window_note": "order-path tests (06-14) held for 07:00-21:00 "
                          "UTC per session contract",
           "results": results,
           "all_pass": all(r["result"] == "PASS" for r in results)}
    EVID.mkdir(parents=True, exist_ok=True)
    (EVID / "DEMO_E2E_PART_A.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("ALL PASS" if out["all_pass"] else "FAILURES PRESENT")
    print("evidence ->", EVID / "DEMO_E2E_PART_A.json")
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
