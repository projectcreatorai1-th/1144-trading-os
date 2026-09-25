# Phase 9 GUI Baseline Repair — Final Report (2026-09-24, UTC)

## 1. Executive Status

```text
GUI REPAIR VALIDATION — BLOCKED
```

Reason: three mandatory gates (F Windows Build, G1 LIVE Safety, G2
Security/Quality Audit) cannot run because their entry-point files
(`tools/verify_build.py`, `tools/p0_live_safety_check.py`,
`tools/security_quality_audit.py`) were deleted by an external modification
of the repository between sessions (see `evidence/gui-repair-live/TOOLING_INCIDENTS.md`).
Recreating them without their original source would fabricate security-gate
tooling, which the rules forbid. Everything else is complete and PASS:
the full live GUI validation (PHASE C 17/17), Phase 9 suites, GUI repair
tests, full pytest (0 failed / 0 errors), and the Architecture Validator
(0 violations).

## 2. Changed Files

Source of Truth: the project is NOT a git repository, so the deterministic
equivalent of `git diff` was used — a full SHA-256 manifest diff of all 31
files in `docs/phase-9/PHASE_9_FILE_HASHES.json` plus new-file detection
(`evidence/gui-repair-live/BASELINE_REPAIR_RECORD.json`).

**PRODUCTION CHANGE (this repair):**

| File | Phase 9 SHA-256 | Current SHA-256 | Reason |
|---|---|---|---|
| `ui/desktop/shell.py` | `ab5e2f71597ee823…` | `c4c920ea0a5f40f9…` | RC-F navigation→preset→visible workspace; RC-2 REJECTED banner + existing-notification reuse; SYMBOL context fix |
| `ui/desktop/run_windows.py` | `0e39c3297108b760…` | `6ab1c541a571529a…` | RC-3 production entry point shows the existing LoginDialog (reuse; no duplicate auth; no bypass) |

**TEST CHANGE (this repair):** `tests/test_phase9_gui_repair.py` (15 tests,
GUI-001..014).

**TEST TOOLING (this repair):** `tools/p9_gui_diagnosis.py`,
`tools/p9_live_gui_test.py` (driver v4), `tools/p9r_gates.py`,
`tools/p9r_baseline_record.py`.

**Externally modified between sessions (NOT part of this repair, flagged,
not investigated per no-scope-expansion):** `architecture/architecture.yaml`,
`identifiers.yaml`, `manifest.yaml`, `schema-registry.yaml`,
`state-machines.yaml`, `architecture/validator/rules.py`,
`core/events/contracts.py`, `platform/api/desktop_gateway.py`; test files
restructured (`test_phase9_desktop_core.py`+`test_phase9_shell.py` →
`test_phase9_core.py`; `test_phase9_e2e.py` → `test_phase9_invariants_e2e.py`);
`architecture/validate.py` relocated to the `architecture.validator` package;
three gate tools deleted. The full pytest suite (which covers the modified
gateway/events/validator) passes, and the live validation exercised the
gateway end-to-end (login, rejections, applied order), so the current tree
is self-consistent — but the external changes are reported, not absorbed.

## 3. RC-3 Login

- **Launch:** `run_windows.py` → exactly one Main Window + exactly one
  LoginDialog (B1 counts = 1/1; HWNDs logged).
- **LoginDialog:** real Win32 window "1144 Trading OS - Login", existing
  implementation reused; no duplicate login system.
- **Invalid:** wrong secret via real keyboard → rejected, dialog closes,
  safe logged-out state; protected actions stay rejected.
- **Cancel:** dialog closed via window-close path → session none; Buy still
  REJECTED (165→2,469 red pixels) — no bypass.
- **Valid:** real secret → dialog closes, session established
  (`SESSION trader@TRADER` visible on statusbar, vision-read evidence).
- **Session:** passes through the existing model/session authority.
- **Duplicate-window check:** valid-login instance has exactly 1 Main
  Window and 0 leftover dialogs; relaunch instance: 1/1 again; zero zombie
  windows system-wide after clean close (exit codes 0).

## 4. RC-F Navigation

Deterministic mapping nav→existing preset via `WorkstationModel.apply_preset()`;
visible title encodes navigation + preset (semantic primary evidence;
pixel diff secondary). Per-destination verbatim titles (vision-read):

| Nav | State | Preset | Visible title (verbatim) | Result |
|---|---|---|---|---|
| Overview | changed | OVERVIEW | `B - Workspace : Overview [preset 'OVERVIEW']` | PASS |
| Markets | changed | MARKET | `B - Workspace : Markets [preset 'MARKET']` | PASS |
| Intelligence | changed | INTELLIGENCE | `B - Workspace : Intelligence [preset 'INTELLIGENCE']` | PASS |
| Portfolio | changed | RISK | `B - Workspace : Portfolio [preset 'RISK']` | PASS |
| Execution | changed | EXECUTION | `B - Workspace : Execution [preset 'EXECUTION']` | PASS |
| Research | changed | RESEARCH | `B - Workspace : Research [preset 'RESEARCH']` | PASS |
| Strategies | changed | RESEARCH | `B - Workspace : Strategies [preset 'RESEARCH']` | PASS |
| Risk | changed | RISK | `B - Workspace : Risk [preset 'RISK']` | PASS |
| Operations | changed | OPERATIONS | `B - Workspace : Operations [preset 'OPERATIONS']` | PASS |

**9/9 PASS**; titles pairwise distinct; no exceptions.

**Reverse navigation** (Overview→Research→Execution→Overview): title matches
each destination reference with 0 mismatch pixels at every hop. PASS.
**Repeat navigation** (Research ×3): titles identical across presses (0 px
diff), Overview intact afterwards (0 px mismatch) — no duplicate widgets,
no handler duplication, no state/visual corruption. PASS.

## 5. RC-2 Rejected Feedback

| Action | REJECTED | Visible feedback | Reason shown | Next step shown | No side effect |
|---|---|---|---|---|---|
| Buy | yes | red banner 165→2,469 px, text changed 5,758 px | `Active authenticated session required.` | `Log in first (persona: trader / risk_manager).` | yes |
| Pause | yes | banner updates in place (121 px text change) | same | same | yes |
| Close Only | yes | banner updates (182 px) | same | same | yes |
| Emergency | yes | banner updates (293 px) | same | same | yes |

Banner lifecycle: constant 37 px red-text height across all four = exactly
one 3-line banner updating — no stacking/duplication. No side effect: zero
APPLIED-green pixels; Overview after the four rejections shows
`orders: 0`, `positions: 0` (vision-read); state-level proof in the pytest
suite (0 orders/positions, REJECTED receipts). Authenticated behavior
unchanged: post-login Buy → green `Buy 0.1: APPLIED`.

## 6. Ctrl+K

```text
PASS
```

Evidence: with the target window's input language normalized to en-US
(language-bar semantics, `WM_INPUTLANGCHANGEREQUEST`), a REAL OS keyboard
event (`keybd_event` Ctrl+K) opens the Command Palette
(`07_command_palette.png`); Escape closes it; layout restored (all switches
logged). Root cause of every prior failure: the machine's Thai keyboard
layout (0x041E) makes Tk assign letter keys keysym `??`, so the binding
cannot match — for physical keyboards too. Binding untouched; 7-probe
evidence chain in `KEYBOARD_LAYOUT_FINDING.md` (follow-up risk documented:
Thai-layout users cannot invoke Ctrl+K).

## 7. Live GUI Evidence

Directory `docs/phase-9/evidence/gui-repair-live/`: 40+ PNGs (launch,
login flows, per-action before/after, 9 nav states, reverse/repeat,
palette, search/resize/minimize, relaunch), 3 vision-read contact sheets
(nav titles, banners, login statusbar), `live_gui_report.json` (every step;
every keyboard/click action logged with action/timestamp/expected_hwnd/
foreground_hwnd/window_title; input refused on mismatch — focus safety
PASS), 4 app instances all exit code 0, runtime exceptions 0, per-instance
stderr logs clean. Pixel evidence is never claimed alone: semantic state is
proven by title-text matching (0-px), vision-read verbatim texts, and the
pytest suite's widget-level assertions.

## 8. Regression

| Gate | Result |
|---|---|
| D1 Phase 9 core tests (restructured file) | PASS — 32 passed |
| D2 Phase 9 invariants/E2E | PASS — 22 passed |
| D3 GUI Repair tests | PASS — 15/15 |
| D4 Full pytest | **PASS — exit 0; 2035 collected; failed 0; errors 0**; skipped: 1 (the long-documented Phase-10 conditional skip, still present) |

Note: an earlier gate run failed on `[WinError 112] disk full` (0 bytes
free; validator fixtures copy the whole project — 9-14 GB temp). Resolved
by clearing `%TEMP%\pytest-of-BANK` + pip cache and redirecting pytest temp
to D:. Final numbers above are from the post-fix clean run.

## 9. Architecture / Build / Safety

- **Architecture Validator: PASS — 0 violations** (`python -m
  architecture.validator`: `STATUS: PASS (failures=0, warnings=0)`).
  Delta vs the previously expected `234/0`: the validator was externally
  relocated/refactored between sessions and its current output no longer
  prints a rule count; the result line is the authoritative current format.
  No GUI-scope violation: production changes are confined to
  `ui/desktop/shell.py` + `ui/desktop/run_windows.py` (presentation +
  entry-point login reuse), confirmed by the manifest diff.
- **Windows Build: BLOCKED** — `tools/verify_build.py` deleted externally.
- **Security Audit / Quality Audit: BLOCKED** —
  `tools/security_quality_audit.py` deleted externally.
- **LIVE Safety: BLOCKED as a gate** (tool deleted), with these facts: this
  repair made no changes to any LIVE-related code path (manifest diff
  proves only the 2 UI files changed by us); LIVE structural refusal was
  not touched; fail-closed behavior is demonstrated live (all four
  protected actions rejected without session; no bypass after cancel).

## 10. BASELINE REPAIR + PHASE 10 STATUS

- Original baseline PRESERVED: `docs/phase-9/PHASE_9_FILE_HASHES.json`
  untouched (all 31 recorded files re-hashed for the diff; recoverable).
- Repair record: `evidence/gui-repair-live/BASELINE_REPAIR_RECORD.json`
  (old vs new SHA-256 per file, PRODUCTION/TEST classification, reasons,
  Phase-10 audit anchors). Labeled `BASELINE REPAIR` — never "Phase 10 MT5
  implementation".
- Phase 10 remains:

```text
IMPLEMENTATION COMPLETE — RUNTIME GATE BLOCKED
```

MT5 transport, connectivity, market-data adapter, reconciliation, runtime
gate, and both runtime findings (server-local epoch; margin_mode enum) are
untouched by this repair and remain separate work items.

STOP.
