# PHASE 9 ARCHITECTURE BASELINE (LOCKED)

## The one authority path

```
Desktop (tkinter A-G shell)
  ↓  (only platform.api + architecture.contracts imports - GUI-001)
DesktopGateway (platform.api.desktop_gateway)
  ↓  (server-side personas; Phase 8 sessions + canonical registry)
Security (Phase 8: Authentication → Authorization)
  ↓
Governance (Phase 8: MakerChecker + GovernanceGate + AuditChain)
  ↓
Core Authority (Phase 3 Policy/Risk · Phase 4 Strategy/Portfolio)
  ↓
OMS / EMS (Phase 5; simulation adapter only)
  ↓
Broker Adapter (adapters.simulation - SYNTHETIC; MT5 unwired)
```

## Authority declarations

- **AI = ADVISORY**: intelligence views are advisory_only, AI WHY is a
  separate labeled explanation system; there is no path from AI output
  to orders, risk decisions or permissions (AI-001..036 + GUI-006).
- **GUI = CONTROL PLANE**: renders projections; dispatches actions;
  holds zero authority (GUI-001..012).
- **Core = Authority**: every Phase 0-8 engine unchanged; the gateway
  composes and delegates, never decides.

## A-G UI architecture (source of truth)

A Navigation/Global Context (Overview, Markets, Intelligence, Portfolio,
  Execution, Research, Strategies, Risk, Operations)
B Main Workspace (tabs, presets OVERVIEW/MARKET/EXECUTION/RESEARCH/
  INTELLIGENCE/RISK/OPERATIONS; PanelDockState DOCKED/FLOATING/PINNED/
  COLLAPSED; size weights; order)
C Persistent Inspector (identity/state/freshness/history; UNKNOWN
  first-class; never infers authority data)
D Activity/Blotter (engine-truth rows; VirtualWindow pagination;
  BlotterFilter global-vs-local never silently overwritten)
E Intelligence/Decision (AI advisory chain + AI WHY; NEWS never becomes
  BUY/SELL)
F Portfolio/Risk (authoritative RiskEngine snapshots: state, decision,
  reasons, limits, gross exposure)
G Action/Approval/Attention (action lifecycle from real receipts;
  confirmation-gated dangerous commands; attention from real state)

Cross-cutting: Global Context Model + ContextStack (back/forward),
Global Search (results establish canonical context), Ctrl+K palette,
notifications (bounded 100), freshness (CURRENT/STALE/UNKNOWN/
DISCONNECTED), UI states (INITIALIZING/LOADING/READY/EMPTY/STALE/
DEGRADED/DISCONNECTED/ERROR/UNKNOWN/PERMISSION_DENIED).

## GUI-001..012 (validator-enforced, each corruption-tested)

GUI-001 desktop imports limited to platform.api + architecture.contracts
  + ui.desktop (stdlib/tkinter excepted)
GUI-002 no GUI engines (Risk/Strategy/Portfolio/Authorization/
  Governance/Audit/Execution Engine classes forbidden)
GUI-003 no direct database access
GUI-004 no direct broker/MT5 path
GUI-005 no direct OMS/EMS imports
GUI-006 no direct intelligence imports (AI reachable only via gateway)
GUI-007 the gateway facade must exist and view models must use it
GUI-008 the shell implements all A-G regions
GUI-009 UNKNOWN + PERMISSION_DENIED are explicit UI states
GUI-010 the shell identifies the environment
GUI-011 dangerous commands refuse to run unconfirmed
GUI-012 workspace recovery to the default layout exists

## What the Desktop is explicitly FORBIDDEN to own

Risk rules · strategy rules · portfolio decisions · authorization
decisions · governance rules · execution logic · audit authority ·
order/position/ledger mutation · direct DB writes · direct broker calls
· any LIVE path · any AI execution path. UI state (workspace_state) is
presentation-only and can never become trading authority.
