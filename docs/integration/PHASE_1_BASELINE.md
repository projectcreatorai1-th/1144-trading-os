# PHASE_1_BASELINE.md — Integration Track Phase 1 (2026-09-26)

Inspection-first report per §2. Everything below is from the REAL tree and
a LIVE runtime probe run before any change (no code was modified for this
phase; the tree is git-clean at `os/v1.0.0` line + session commits).

## สิ่งที่มีอยู่จริง (verified)

- Kernel/contracts: identifiers registry 1.11.0 · schema registry · state
  machines incl. `connection_state` (§4 states) + `gateway_session` ·
  Architecture Validator (234+ rules, currently PASS 0 violations)
- Data pipeline (Phase 1): IngestionPipeline (raw→normalized→event) with
  dedup/ordering/quality/lineage — REUSE target per §5
- Reconciliation (Phase 2): ReconciliationEngine + MT5 BrokerSnapshot —
  REUSE target per §10
- EMS/OMS (Phase 5) + MT5 transport/adapter (Phase 10): execution path
  with EXEC-003 capability gate (margin_mode fixed against the real enum,
  2026-09-25) — REUSE target per §8
- Connectivity plane: ConnectionMonitor/ConnectivityConfig
  (architecture/connectivity.yaml; reconnect backoff 500ms→8s)
- MT5TickSource: server-local epoch FIXED with measured offset conversion
  (+2s justified clock-skew tolerance; contract 1.1.0) — §6 time
  governance already canonical, with regression tests
- DesktopGateway + platform.gateway (SNIPER Gateway Server, contract
  v1.0.0, WebSocket+InProcess) — integration transport layer
- Workstation (Phase 9, baseline locked gen-2) + GUI repair live-validated
- Failure coverage (§13): chaos suite (duplicate/out-of-order/stale
  ticks, db/disk/audit failures), Phase 10 core suites (disconnect/
  reconnect, unknown symbol, LIVE refusal), finding-fix tests
- Test baseline: full suite 2155 collected / 0 failed / 0 errors /
  1 documented conditional skip (2026-09-25 run)
- Trace: correlation/trace ids through contracts; TraceAssembler with
  gap reporting (Phase 14 work); audit chain append-only
- Time governance: ClockQuality records measured server offsets (the
  3h EET skew re-measured live today: tick epoch 1790371953 vs UTC
  1790361153 = exactly +10800s — matches the canonical fix)

## สิ่งที่ reuse ได้ (§5/§8/§10 — no parallel implementations exist)

MT5MarketDataAdapter → existing pipeline; EMS/transport for DEMO
execution; ReconciliationEngine for broker snapshots; audit/trace infra;
Workstation status surfaces (§12 wiring points exist in Operations/
Overview presets).

## สิ่งที่ขาด / ยัง BLOCKED

- **DEMO ACCOUNT LOGIN — the phase blocker (§3 D/E)**: live probe today:
  `initialize()` OK; login 411173797; **trade_mode = 2 =
  ACCOUNT_TRADE_MODE_REAL** (0=DEMO); server XMGlobal-MT5 16; margin_mode
  2 (HEDGING, mapping already correct); **terminal `trade_allowed=False`**
  (AutoTrading disabled); EURUSD ticks streaming (real market data).
  → §3 prerequisite D (DEMO account availability at the terminal) and E
  (DEMO login/session) FAIL. Per §1.14: STATUS = RUNTIME GATE BLOCKED.
- DEMO-labeled runtime evidence (§15 TEST-01..14) cannot be produced on a
  REAL-account session (execution paths correctly refuse; market-data-only
  facts from the 2026-09-25 gate rerun are real-terminal but not
  DEMO-labeled and are NOT counted as DEMO evidence).

## files ที่จะเปลี่ยน (เมื่อ unblock)

None required for connectivity itself (all §4–§11 components exist).
This phase would add only: evidence files under
docs/integration/evidence/phase-1/ and the runtime reports; optionally a
§12 status wiring refresh in the Operations preset if runtime shows gaps.

## files ที่ห้ามเป้ายังไม่มีการเปลี่ยนแปลง production ใด ๆ ใน phase นี้ (BLOCKED ก่อน implement)

## dependency prerequisites

Human/terminal-side: log the MT5 terminal into a DEMO account (terminal
credentials UI) and enable AutoTrading if demo order tests are to run.
Everything machine-side is ready.

## current versions

OS system 1.1.0 · contracts 1.10/1.11 (identifiers) · Gateway contract
1.0.0 · tick feed contract 1.1.0 · architecture registry synced
(manifest/architecture/state-machines/identifiers)

## current test baseline

Full: 2155 collected / 0 failed / 0 errors / 1 documented skip
(2026-09-25). Fresh targeted run for this phase (phase10 core + finding
fixes + chaos + GUI repair + gateway server): see
PHASE_1_FINAL_REPORT.md §Testing.
