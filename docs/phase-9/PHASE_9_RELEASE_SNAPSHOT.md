# PHASE 9 RELEASE SNAPSHOT

Release Name:
1144 Trading OS — Desktop Trading Workstation

Status:
PASS

Architecture:
A–G Workstation

Authority:
Core

Desktop:
Control Plane

AI:
Advisory

Security:
Phase 8 Authority

Risk:
Existing RiskEngine

Execution:
Existing OMS/EMS

Environment:
Simulation / Demo / Live separated (LIVE structurally refused at the
gateway constructor)

Tests:
See PHASE_9_BASELINE.json — measured at freeze time by a full
`python -m pytest` run plus the architecture validator; never copied
from earlier reports.

Build:
`python ui/desktop/run_windows.py --verify-build` — measured at freeze
time (9 checks: tkinter runtime, gateway startup, authentication,
workspace persistence, default workspace, event handling + dedup,
order chain, fail-closed disconnect, shutdown).

Launcher:
`python ui/desktop/run_windows.py`
