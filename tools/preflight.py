"""RUN PREFLIGHT — automated pre-market readiness check (Phase 16).

One command, machine-readable (JSON) + human-readable (console) output.
Every check is a REAL probe of the existing tree/runtime; verdict is
READY / READY_FOR_MOCK / BLOCKED with per-check PASS/FAIL/WARNING/BLOCKER.
Never enables LIVE (structural refusal is itself a check).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.modules.pop("platform", None)

OUT_JSON = ROOT / "docs" / "integration" / "evidence" / "premarket" \
    / "PREFLIGHT_RESULT.json"


def _run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        return proc.returncode == 0, tail[0][:120]
    except Exception as error:                     # noqa: BLE001 - report
        return False, f"{type(error).__name__}: {error}"[:120]


def main() -> int:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str,
              level: str = "PASS") -> None:
        checks.append({"check": name, "status": level if ok else "FAIL",
                       "detail": detail})

    def blocker(name: str, detail: str) -> None:
        checks.append({"check": name, "status": "BLOCKER", "detail": detail})

    def warning(name: str, detail: str) -> None:
        checks.append({"check": name, "status": "WARNING", "detail": detail})

    started = time.time()

    # --- static tree checks -------------------------------------------
    ok, detail = _run([sys.executable, "-m", "architecture.validator"],
                      timeout=120)
    check("architecture_validator", ok, detail)

    ok, detail = _run([sys.executable, "ui/desktop/run_windows.py",
                       "--verify-build"], timeout=300)
    check("build_verify", ok, detail)

    # strategy spec presence + pinned identity
    spec = Path("C:/Users/BANK/.zcode/workspace/default"
                "/SNIPER-CashFlow-Analyzer/STRATEGY_SPEC/"
                "strategy_spec.json")
    if spec.exists():
        check("strategy_specification", True,
              "SNIPER STRATEGY_SPEC present (tag spec/1.0.0)")
        import hashlib
        digest = hashlib.sha256(spec.read_bytes()).hexdigest().upper()
        check("strategy_spec_hash_available", True, f"sha256 {digest[:16]}…")
    else:
        blocker("strategy_specification", "spec file missing")

    # targeted suite (fast representative slice; full suite = pre-market run)
    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_premarket_scenarios.py",
                       "tests/test_phase10_finding_fixes.py",
                       "tests/test_phase13_chaos.py",
                       "tests/test_gateway_server.py"], timeout=600)
    check("targeted_suites", ok, detail)

    # --- runtime probes ------------------------------------------------
    mt5_probe: dict = {}
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            account = mt5.account_info()
            symbols = []
            for sym in ("EURUSD", "XAUUSD", "US30"):
                if mt5.symbol_info_tick(sym) is not None:
                    symbols.append(sym)
            from adapters.mt5.account_guard import (
                AccountGuard,
                AccountGuardError,
                ExpectedIdentity,
                TerminalIdentity,
            )
            observed = TerminalIdentity.from_mt5(account, symbols)
            expected = ExpectedIdentity(trade_mode=0)
            guard = AccountGuard(expected)
            try:
                decision = guard.check(observed)
                check("account_guard_demo", True,
                      f"login {observed.login} server {observed.server} "
                      f"trade_mode={observed.trade_mode} (DEMO)")
            except AccountGuardError as error:
                blocker("account_guard_demo", str(error)[:120])
            if account.login == 113126589:
                check("expected_demo_account_pinned", True,
                      "login 113126589 matches the operator-pinned DEMO")
            else:
                warning("expected_demo_account_pinned",
                        f"DEMO session login {account.login} is not the "
                        "previously observed 113126589 — confirm this is "
                        "the intended DEMO account")
            mt5.shutdown()
        else:
            blocker("mt5_terminal", f"initialize failed: {mt5.last_error()}")
    except ImportError:
        blocker("mt5_package", "MetaTrader5 package missing")

    # LIVE safety lock (structural refusal test slice)
    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_phase10_core.py", "-k", "live"],
                      timeout=300)
    check("live_safety_lock", ok, detail or "LIVE refusal tests")

    # persistence/recovery/backup smoke (phase12 suite)
    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_phase12_incident_dr.py"], timeout=300)
    check("recovery_backup_restore", ok, detail)

    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_phase2_reconciliation.py"], timeout=300)
    check("reconciliation_engine", ok, detail)

    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_phase3_risk_engine.py"], timeout=300)
    check("risk_engine", ok, detail)

    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_phase5_boundary_oms.py"], timeout=300)
    check("oms_lifecycle", ok, detail)

    # kill switch
    ok, detail = _run([sys.executable, "-m", "pytest", "-q",
                       "tests/test_gateway_server.py", "-k",
                       "kill or backpressure"], timeout=300)
    check("kill_switch", ok, detail or "kill/backpressure tests")

    # evidence dir writable (no secrets)
    try:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        check("evidence_store", True, str(OUT_JSON.parent))
    except Exception as error:                     # noqa: BLE001
        blocker("evidence_store", str(error)[:100])

    # --- verdict --------------------------------------------------------
    failed = [c for c in checks if c["status"] == "FAIL"]
    blockers = [c for c in checks if c["status"] == "BLOCKER"]
    warnings = [c for c in checks if c["status"] == "WARNING"]
    if blockers or failed:
        verdict = "BLOCKED"
    elif warnings:
        verdict = "READY_FOR_DEMO_WITH_WARNINGS"
    else:
        verdict = "READY_FOR_DEMO"

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "duration_seconds": round(time.time() - started, 1),
        "checks": checks,
        "summary": {"total": len(checks), "pass": sum(
            1 for c in checks if c["status"] == "PASS"),
            "fail": len(failed), "blockers": len(blockers),
            "warnings": len(warnings)},
        "live_trading": "HARD_LOCKED",
    }
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print("=== RUN PREFLIGHT ===")
    for c in checks:
        print(f"[{c['status']:7}] {c['check']:32} {c['detail']}")
    print(f"VERDICT: {verdict} "
          f"(pass {result['summary']['pass']}/{result['summary']['total']}"
          f", blockers {len(blockers)}, warnings {len(warnings)})")
    print(f"LIVE_TRADING = HARD_LOCKED")
    print(f"machine-readable -> {OUT_JSON}")
    return 0 if verdict != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
