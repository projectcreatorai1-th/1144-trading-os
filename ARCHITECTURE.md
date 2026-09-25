# ARCHITECTURE.md — 1144 Trading OS

**Role:** Gateway server + core engines + MT5 adapter + desktop runtime of
the 1144 trading ecosystem. Contract-first: the single source of truth for
all architecture rules is `architecture/architecture.yaml` (validated by
`python -m architecture.validator`, 234+ rules, 0 violations).

## Authority chain (as built)

```
SNIPER CashFlow Analyzer  (RESEARCH authority — separate repo)
      ↕  signal/evidence enters as research data + STRATEGY_SIGNAL_CREATED
Desktop GUI (ui.desktop — CONTROL PLANE; never touches MT5/events, GF-002)
      ↕  DesktopGateway (platform.api — TRANSPORT)
Phase 8 Security → Phase 3 Policy/Risk → Phase 4 Strategy/Portfolio
      (STRATEGY + RISK + EXECUTION authority — deterministic core engines;
       LIVE structurally refused)
Phase 5 OMS/EMS → MT5 adapter (adapters.mt5 — DEMO-bound transport)
      ↓
MT5 terminal (RUNTIME) → Broker (MARKET EXECUTION)
```

## Layers (dependency policy, `architecture.yaml`)

| Layer | Contents |
| --- | --- |
| contracts_kernel (0) | `architecture/` — identifiers, schemas, state machines, tolerances, registry |
| ui (1) | `ui/desktop` (live-validated workstation GUI) · `ui/web` (reserved placeholder, empty) |
| api (2) | `platform/api` — DesktopGateway, connectivity plane, desktop actions/feed |
| core (3) | `core/` — data, reconciliation, policy/risk, strategy/portfolio, OMS/EMS, ledger, intelligence, security |
| infrastructure (4) | `platform/` (gateway server, event bus, database, backup, chaos, incident, monitoring, …) + `adapters/` (mt5, broker, market_data, news, calendar, ai) |

Key global rules: GF-001 (nothing depends on ui except ui domains),
GF-002 (GUI never depends on database/event bus/adapters directly).

## Environments

RESEARCH · SIMULATION · REPLAY · PAPER · BACKTEST · DEMO · LIVE — with LIVE
structurally refused on every execution path.

## Ecosystem position

This repository serves the ONE gateway (contract v1.0.0) for SNIPER and
OUR-EA clients; see `THREE_REPO_PROVENANCE_AUDIT.md` for the full chain and
`COMPATIBILITY_MATRIX.md` for version compatibility.
