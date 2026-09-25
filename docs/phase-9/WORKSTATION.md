# Workstation Behavior (A-G)

## A - Navigation / Context
Primary items: Overview, Markets, Intelligence, Portfolio, Execution,
Research, Strategies, Risk, Operations. Selecting A changes the B-G
workspace context. Watchlist selection uses the same Global Context
Model (no second market-data authority).

## B - Main Workspace
Tabs + presets (OVERVIEW/MARKET/EXECUTION/RESEARCH/INTELLIGENCE/RISK/
OPERATIONS); panels carry dock state (DOCKED/FLOATING/PINNED/COLLAPSED),
size weights and order; the layout persists as UI-only workspace_state
and corrupted state recovers to the default A-G layout.

## C - Inspector
Persistent, contextual. Shows identity, state, freshness, last price
(SYNTHETIC-labeled), context history trail. Missing authority data is
UNKNOWN - never inferred, never fabricated.

## D - Activity / Blotter
Orders table with real engine states; GLOBAL CONTEXT vs LOCAL VIEW
FILTER are distinct objects - global selection rebinds only when no
local override exists, and clear_local returns to the global binding.
Large lists use a bounded VirtualWindow (page + next/prev).

## E - Intelligence / Decision
The advisory chain: SYNTHETIC observations -> point-in-time features ->
deterministic classifier -> advisory proposal. AI WHY is labeled as
AI_WHY and is a different object from CORE WHY (Intent -> Portfolio ->
RiskContext -> Policy -> Rule -> RiskDecision -> OMS -> ... -> Audit).
News never directly becomes BUY/SELL.

## F - Portfolio / Risk
Authoritative RiskEngine output: risk state machine value, decision
(ALLOW/BLOCK/...), reasons, limits, gross exposure. UNKNOWN/STALE/
DISCONNECTED never render as safe/current.

## G - Action / Approval / Attention
Action lifecycle REQUESTED -> real engine receipt (APPLIED/REJECTED/
FAILED/UNKNOWN); button click is never success. Dangerous commands
(pause/close-only/emergency) require explicit confirmation and the
matching permission (trader cannot emergency-stop; risk manager can).
Attention items derive from real state: feed staleness, pending
approvals, risk state, open security incidents.

## Interaction
Global search (orders/positions/symbols; opening a result establishes
canonical context), Ctrl+K palette (navigation + guarded commands),
Esc/Enter/arrow flows in the palette, notification bell with unread
count and bounded history (100), context stack with back/forward.
