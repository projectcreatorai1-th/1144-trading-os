# 1144 Trading Workstation — GUI Repair Final Report

Date: 2026-09-25 (UTC) · Scope: Phase 9 GUI interaction repair (RC-2, RC-3, RC-F)
Evidence root: `docs/phase-9/evidence/gui-repair-live/`

## 1. Executive Status

```text
GUI REPAIR VALIDATION — BLOCKED
```

The repair itself is implemented and fully verified: live GUI validation
17/17 PASS (real OS input, production `pythonw` entry), automated suites
PASS (GUI repair 15/15; full pytest exit 0 with 0 failed / 0 errors),
Architecture Validator PASS (0 violations), Windows Build PASS (relocated
verifier, 9/9), LIVE Safety PASS (relocated LIVE-refusal tests). The single
blocker is **G2 Security/Quality Audit**: its standalone tool
(`tools/security_quality_audit.py`) was deleted by an external repository
modification; an exhaustive search (Section 16) found no recoverable
original; recreating it from memory is forbidden. Partial static coverage
exists via the SEC-* rules inside the passing validator.

## 2. Root Cause → Fix mapping

| Root cause | Fix | File |
|---|---|---|
| RC-3: desktop entry `run_windows.launch()` had no LoginDialog | REUSE existing `LoginDialog` from `app.main()` flow in launch() | `ui/desktop/run_windows.py` |
| RC-F: `_navigate()` changed state but not the visible workspace | `NAV_PRESET` mapping (9 items, preset names verified from source) → existing `apply_preset()` → visible B title/content render | `ui/desktop/shell.py` |
| RC-2: REJECTED receipts produced no visible feedback (ViewModel catches ContractError by design) | presentation-only `_show_rejection()` on existing banner label + existing notification infra; real reason from receipt | `ui/desktop/shell.py` |

ViewModel (`viewmodels.submit_order()/run_command()`), Risk, Authority,
OMS/EMS, MT5, Connectivity: **untouched** (hash manifest proves).

## 3. Changed Files

Production (this repair): `ui/desktop/run_windows.py`, `ui/desktop/shell.py`.
Tests: `tests/test_phase9_gui_repair.py` (15 tests).
Tooling: `tools/p9_gui_diagnosis.py`, `tools/p9_live_gui_test.py` (v4),
`tools/p9r_gates.py`, `tools/p9r_baseline_record.py`.
External changes detected and NOT absorbed: see Section 18.

## 4. Before/After SHA-256

| File | Phase 9 baseline | Repair layer | Current |
|---|---|---|---|
| `ui/desktop/shell.py` | `ab5e2f71597ee823…` | `c4c920ea0a5f40f9…` | `f34b750bc0c047b8…` (second external layer; repair verified intact) |
| `ui/desktop/run_windows.py` | `0e39c3297108b760…` | `6ab1c541a571529a…` | `0d3f0cede33915ba…` (external layer also restored `--verify-build`) |

Full record: `BASELINE_REPAIR_RECORD.json` (31-file manifest diff).
Original `PHASE_9_FILE_HASHES.json` untouched and recoverable.

## 5. Automated Test Results

- GUI repair suite: **15/15 PASS** (login lifecycle, 9 nav paths + reverse +
  repeat, 4 rejections with visible feedback + no side effects, success
  path Buy→APPLIED, existing components, binding registration).
- Phase 9 core (restructured): **32/32 PASS** · invariants/E2E: **22/22 PASS**.
- Full pytest: **exit 0 — 2034 passed, 1 skipped, 0 failed, 0 errors**
  (2035 collected; the split is pinned: exit 0 excludes failed/errors, the
  tree contains zero xfail markers, and the single skip is verified active —
  `SKIPPED [1] tests/test_phase10_core.py:474: package present; refusal
  path untestable`, the pre-existing documented Phase 10 conditional skip,
  unrelated to this repair). One chained run showed a transient
  display-related skip in the GUI suite; deterministic direct rerun = 15/15
  with zero skips.

## 6. Live GUI Results (driver v4, real OS input, pythonw entry)

```text
LoginDialog launch            PASS   (exactly 1 Main + 1 Dialog)
Invalid Login                 PASS   (rejected, fail-closed, safe state)
Cancel Login                  PASS   (session None; Buy still REJECTED)
Valid Login                   PASS   (SESSION trader@TRADER visible)
Navigation                    9/9    (visible change + semantic title match)
Reverse Navigation            PASS   (O→R→E→O, 0-px title mismatch each hop)
Repeat Navigation             PASS   (Research ×3 stable, no corruption)
Buy/Pause/CloseOnly/Emergency 4/4    (visible REJECTED banner each)
Rejection no-side-effect      PASS   (green=0; orders 0; positions 0)
Existing GUI components       PASS   (search/resize/minimize/restore/close)
Ctrl+K                        PASS   (real keyboard; palette opens/closes)
Focus safety                  PASS   (HWND verified+logged before every input)
Clean close/relaunch          PASS   (exit 0; no zombie; no stale session)
Runtime exceptions            0      (4 instances, clean stderr)
```

## 7. Login Evidence

`01_login_dialog.png` (real Win32 dialog over the shell), per-flow shots
`02`/`08-10`/`11-14`/`19-21_*.png`, `login_evidence_sheet.png`
(vision-read: `NOT LOGGED IN` → `SESSION trader@TRADER` → green
`Buy 0.1: APPLIED`), duplicate counts and zombie checks in
`live_gui_report.json`.

## 8. Navigation 9/9 Evidence

`nav_title_sheet.png` (verbatim titles per destination incl. presets:
OVERVIEW, MARKET, INTELLIGENCE, RISK, EXECUTION, RESEARCH, RESEARCH, RISK,
OPERATIONS), `06_nav_*.png`, per-item pixel diffs 2,953–11,468 (secondary),
pairwise-distinct titles, reverse/repeat shots `06r_*`/`06p_*` with 0-px
mismatches, semantic state asserted in the pytest suite (primary).

## 9. Rejection Feedback Evidence

`feedback_banner_sheet.png` — verbatim per action:
`ACTION REJECTED — <Action>` / `Reason: Active authenticated session
required.` / `Next step: Log in first (persona: trader / risk_manager).`
Red pixels 165→2,469–2,554; banner updates in place (text-change evidence
121–5,758 px per action); red-text bbox constant 37 px = single banner, no
stacking; `04/05_*.png` before/after each action.

## 10. No-Side-Effect Evidence

State-level (not pixel-only): pytest asserts 0 orders / 0 positions and
REJECTED receipts for all four actions; live Overview content after the
four rejections (vision-read `overview_after_rejections.png`):
`orders: 0`, `positions: 0`, `session: NOT LOGGED IN`, `notifications: 4`;
zero APPLIED-green pixels across all rejected clicks.

## 11. Ctrl+K Evidence

Real `keybd_event` Ctrl+K after verified foreground + en-US layout
normalization (language-bar semantics; layout restored; all switches
logged) → Command Palette window opens (`07_command_palette.png`), Escape
closes it. Root cause of all prior failures: Thai keyboard layout (0x041E)
→ Tk keysym `??` (affects physical keyboards too). Binding untouched;
7-probe chain in `KEYBOARD_LAYOUT_FINDING.md`; recorded as known
environmental behavior with a documented follow-up risk.

## 12. Window Lifecycle Evidence

Resize 1280×800→1084×741→restored (render intact, 0-px title diff);
minimize (IsIconic)→restore (0-px); close exit 0 ×4 instances; zero zombie
windows system-wide; relaunch = exactly 1 Main + 1 LoginDialog; no stale
session (fresh-instance Buy REJECTED). Shots `17`–`21_*.png`.

## 13. Regression Results

D1 core 32/32 · D2 invariants/E2E 22/22 · D3 GUI repair 15/15 · D4 full
pytest **exit 0, 0 failed, 0 errors** (2035 collected; 1 documented
pre-existing skip, see §5) · recorded in `regression_gates.json`
(+ `gates_run.log`).

## 14. Architecture Validator

**PASS — `STATUS: PASS (failures=0, warnings=0)`** via `python -m
architecture.validator` (the validator was relocated externally into the
`architecture.validator` package; current output no longer prints the old
"234 rules" count line — delta explained, not forced). All GUI-scope
production changes confined to the two permitted files.

## 15. Build Verification

**PASS — 9/9** (`python ui/desktop/run_windows.py --verify-build`, the
relocated Phase 9 SECTION 74 verifier: tkinter runtime, gateway startup,
authentication, workspace persistence, default workspace, event handling +
dedup, order chain, fail-closed disconnect, shutdown → `BUILD PASS`). The
original `tools/verify_build.py` wrapper was deleted externally; the
verifier itself survived inside the production launcher.

## 16. LIVE Safety

**PASS via relocated checks that actually ran**: (a) architecture validator
LIVE-REFUSED rules PASS; (b) Phase 10 core LIVE-refusal tests — including
construction-time refusal of `ConnectionMonitor(environment="LIVE")` and
`MT5ReconciliationService(environment="LIVE")` — PASS inside full pytest
exit 0 (the single skip is the documented "package present" conditional,
not a LIVE-permission test). The standalone wrapper
(`tools/p0_live_safety_check.py`) was deleted externally; not recreated.
LIVE remains structurally refused; no LIVE code path touched by this
repair (manifest-proven).

## 17. Security/Quality Audit

**BLOCKED.** `tools/security_quality_audit.py` deleted externally.
Section-16 search performed and exhausted: `platform/backup` (only
`__init__.py`), the `1144/` tree incl. `Releases/V5-PRODUCTION-FROZEN`,
Recycle Bin (`$R*.py` scan, all SIDs), Desktop/Documents/Downloads, D:/F:
roots, and every desktop-shortcut target (all point to unrelated projects
or the current repo). Not found; NOT recreated from memory (no fake
tools). Partial static coverage that DID run: SEC-* rules inside the
passing architecture validator.

## 18. External Changes

Detected by 31-file manifest diff (recorded, never overwritten):
`architecture/architecture.yaml`, `identifiers.yaml`, `manifest.yaml`,
`schema-registry.yaml`, `state-machines.yaml`,
`architecture/validator/rules.py`, `core/events/contracts.py`,
`platform/api/desktop_gateway.py` — modified externally between sessions;
plus test-file restructure, validator relocation, build-verifier
relocation into `run_windows.py`, deletion of three `tools/` wrappers, and
a second external edit layer on both repaired production files. Impact:
repair functionality verified intact against current files (15/15 + 17/17
live); full pytest covers the modified modules and passes. None of these
changes were required by, or made by, this repair. Details:
`TOOLING_INCIDENTS.md`, `BASELINE_REPAIR_RECORD.json`.

## 19. Phase 10 Boundary Verification

`IMPLEMENTATION COMPLETE — RUNTIME GATE BLOCKED` — unchanged. MT5 runtime/
transport/execution, market-data adapter, reconciliation, connectivity
plane, runtime gate, server-local epoch finding, margin_mode finding: zero
writes issued this repair; current hashes recorded as audit anchors.
No MT5 install, no LIVE enablement attempted.

## 20. Known Limitations

1. Ctrl+K unreachable under Thai (any non-Latin) keyboard layout — Tk
   keysym limitation, documented; binding unchanged per rules.
2. G2 audit tool unrecoverable (Section 17) — final status remains BLOCKED
   until an original artifact surfaces or the owner re-issues the tool.
3. Disk pressure incident (C: hit 0 bytes; validator fixtures copy the
   whole project per test). Mitigated by redirecting pytest temp to D:;
   systemic cleanup of C: is the owner's call.
4. External repo edits during the repair window are outside my control;
   they are recorded, not reverted.

## 21. Final Status

```text
RC-3 Login: PASS
RC-F Navigation: PASS
RC-2 Rejection Feedback: PASS
Ctrl+K: PASS
Live GUI: PASS
Automated Regression: PASS
Architecture: PASS
Build: PASS
LIVE Safety: PASS
Security/Quality: BLOCKED
Baseline Integrity: PASS
Phase 10 Boundary: PASS

FINAL: GUI REPAIR VALIDATION — BLOCKED
```

Blocker: G2 Security/Quality Audit (tool deleted externally; search
exhausted; recreation forbidden). Everything else passed with evidence.
