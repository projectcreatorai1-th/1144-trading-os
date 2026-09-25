# PHASE_1_FINAL_REPORT.md — Integration Track Phase 1 (2026-09-26)

```text
STATUS = BLOCKED
```

Per §1.14/§17: the MT5 DEMO prerequisites failed at items D/E and the
exit gate "[ ] DEMO account verified" cannot close. No runtime PASS was
claimed; no synthetic evidence was produced.

## ROOT_CAUSE
The MetaTrader 5 terminal is logged into a **REAL account** — not a DEMO
account. This is a terminal-side login state, not a code defect.

## DEPENDENCY
Human action at the MT5 terminal: switch the login to a DEMO account
(and enable AutoTrading for the demo-order tests). Machine-side,
everything required by §4–§13 already exists and is tested.

## EXACT_BLOCKER
Live probe 2026-09-26 (evidence/phase-1/PREREQUISITE_PROBE_2026-09-26.json):
login 411173797 · `trade_mode = 2 = ACCOUNT_TRADE_MODE_REAL` (requires 0
= DEMO) · server XMGlobal-MT5 16 · `trade_allowed = False`. Prerequisites
A/B/C/F/H/I/J/K/L PASS; **D/E/G/M/N FAIL**.

## EVIDENCE
- Prerequisite probe JSON (above) — no credentials stored
- 2026-09-25 gate rerun (docs/phase-10/PHASE_10_GATE_RERUN_2026-09-25.md):
  real-terminal connectivity/ticks/pipeline/freshness PASS + correct
  REAL-account execution refusal (market-data facts; NOT counted as DEMO
  evidence)
- Section-18 artifact set in this directory, each stating exactly what is
  component-verified vs runtime-BLOCKED

## RESUME_FROM
§3 prerequisite re-probe (one command). On DEMO login: run the §15
TEST-01..14 controlled sequence, then close the §17 checklist; only
§12 workstation wiring may need a touch-up based on what the runtime
shows — no other production changes are expected.

## What WAS verified this run (honest scope)
- §2 baseline: PHASE_1_BASELINE.md (real tree; git-clean; versions listed)
- §4 connection machine, §5 market-data path, §6 time governance
  (re-measured live: +10800s EET, canonical fix + 15/15 tests), §9 margin
  mode (2=HEDGING verified live), §10 reconciliation components,
  §11 trace assembler, §13 runnable failure modes, §16 LIVE lock — all
  component-level PASS via existing suites
- Testing (§14): targeted suites (phase-10 core + finding-fixes + chaos +
  GUI repair + gateway server) = **112 passed / 0 failed / 0 errors / 1
  documented conditional skip** · Architecture Validator **PASS 0
  violations** · Windows Build **PASS 9/9** · full-suite baseline
  unchanged at 2155/0/0/1-skip (2026-09-25); no coverage reduced, no
  tests removed
- No production files changed in this phase (BLOCKED before implement);
  artifacts only (this directory)

## NEXT PHASE LOCK (§20)
Phase 2 (SNIPER Strategy Specification / OUR EA work) NOT started.
LIVE = LOCKED throughout. No OUR EA. No Phase 2. Stop here.
