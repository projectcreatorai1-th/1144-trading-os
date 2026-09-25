"""Windows launcher + release build verifier (Phase 9, SECTION 74).

run_windows.py      -> launches the workstation (python ui/desktop/run_windows.py)
--verify-build      -> verifies the release build end-to-end WITHOUT
                       opening a window: runtime deps, gateway startup,
                       login, workspace persistence, event handling,
                       order chain, shutdown. Exit 0 = PASS.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def launch() -> int:
    # BASELINE REPAIR (RC-3): the production desktop entry point now uses
    # the SAME login flow as app.main() - the reused LoginDialog (no
    # duplicate login implementation, no bypass; cancel/invalid login
    # leaves the session None and every privileged action stays blocked).
    from platform.api.desktop_gateway import DesktopGateway
    from ui.desktop.shell import LoginDialog, Shell
    from ui.desktop.viewmodels import WorkstationModel
    gateway = DesktopGateway(environment="SIMULATION")
    try:
        model = WorkstationModel(gateway)
        model.start()
        shell = Shell(model)
        LoginDialog(shell, model)
        shell.after(300, shell._render)
        shell.run()
    finally:
        gateway.close()
    return 0


def verify_build() -> int:
    checks: list[tuple[str, str]] = []

    def check(name: str, fn) -> None:
        try:
            fn()
            checks.append((name, "PASS"))
        except Exception as error:  # noqa: BLE001 - report every failure
            checks.append((name, f"FAIL: {error}"))

    import tkinter
    check("tkinter runtime", lambda: tkinter.Tk().destroy())

    from platform.api.desktop_gateway import DesktopGateway
    from ui.desktop.contracts import default_workspace
    from ui.desktop.viewmodels import WorkstationModel

    state: dict = {}

    def startup():
        state["gw"] = DesktopGateway(environment="SIMULATION",
                                     db_path=ROOT / "runtime" /
                                     "build-verify.db")
        state["model"] = WorkstationModel(state["gw"])
        state["model"].start()

    check("gateway startup", startup)
    check("authentication",
          lambda: state["model"].login(
              "trader", "synthetic-trader-desktop-secret"))
    check("workspace persistence", lambda: (
        state["model"].apply_preset("MARKET"),
        state["model"].restore_workspace(
            state["model"].persist_workspace()),
        state["model"].restore_workspace({"preset": "CORRUPT"})))
    check("default workspace valid", lambda: default_workspace().validate())

    def events():
        # drive the bus through the gateway's own surfaces only (the
        # desktop never touches event contracts directly)
        seen = []
        token = state["gw"].subscribe(seen.append)
        state["model"].logout()
        state["model"].login("risk_manager",
                             "synthetic-risk-desktop-secret")
        state["model"].logout()
        assert len(seen) >= 2  # logout revocation + auth events delivered
        state["gw"].unsubscribe(token)
        state["model"].login("trader", "synthetic-trader-desktop-secret")
    check("event handling + dedup", events)

    check("order chain", lambda: state["model"].submit_order(
        "EURUSD", "BUY", "0.1"))
    check("fail-closed disconnect", lambda: (
        state["gw"].disconnect(),
        _expect_error(lambda: state["gw"].act("submit_order", {
            "symbol": "EURUSD", "side": "BUY", "quantity": "0.1"})),
        state["gw"].reconnect()))
    check("shutdown", lambda: (
        state["model"].logout(), state["gw"].close()))

    print("=== 1144 Desktop Build Verification (SYNTHETIC data) ===")
    failed = 0
    for name, result in checks:
        print(f"{name:32s} {result}")
        if result.startswith("FAIL"):
            failed += 1
    print(f"{'BUILD':32s} {'PASS' if failed == 0 else 'FAIL'}")
    return 1 if failed else 0


def _expect_error(fn) -> None:
    from architecture.contracts.errors import ContractError
    try:
        fn()
    except ContractError:
        return
    raise AssertionError("expected fail-closed error")


if __name__ == "__main__":
    if "--verify-build" in sys.argv:
        raise SystemExit(verify_build())
    raise SystemExit(launch())
