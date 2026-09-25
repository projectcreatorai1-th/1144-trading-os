"""1144 Trading OS - desktop application entry point (Phase 9).

Launch: python -m ui.desktop.app

Startup follows SECTION 60: Initialize UI -> load configuration ->
authenticate -> connect Core -> validate environment/permissions ->
subscribe events -> load workspace -> synchronize -> READY.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platform.api.desktop_gateway import DesktopGateway  # noqa: E402
from ui.desktop.shell import LoginDialog, Shell  # noqa: E402
from ui.desktop.viewmodels import WorkstationModel  # noqa: E402


def main(environment: str = "SIMULATION") -> int:
    gateway = DesktopGateway(environment=environment)
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


if __name__ == "__main__":
    raise SystemExit(main())
