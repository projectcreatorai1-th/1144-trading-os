# Live GUI Validation — BASELINE REPAIR (driver v4, final-validation command)

**Result: ALL PASS — PHASE C acceptance table complete (17/17).**

Driver: `tools/p9_live_gui_test.py` (v4). All input is REAL OS input
(pywinauto SendInput for text/Enter, ctypes `keybd_event` for Ctrl+K/Escape,
SendInput mouse for clicks) with deterministic foreground verification:
every keyboard/click action logs `action / timestamp / expected_hwnd /
foreground_hwnd / window_title`, and input is REFUSED on mismatch
(cross-window input SAFE). Four application instances were launched and all
exited cleanly (exit code 0, no stderr tracebacks -> runtime exceptions 0).

## B1 — Clean launch / duplicates

`run_windows.py` -> exactly one "1144 Trading OS" Main Window (count=1) and
exactly one "1144 Trading OS - Login" LoginDialog (count=1). HWNDs recorded
in `live_gui_report.json` (step A/B1).

## B2 — Invalid login

Real keyboard: activate dialog -> click secret field -> Ctrl+A -> type wrong
secret -> Enter. Result: authentication rejected, dialog closes, app remains
in safe logged-out state. Protected actions stay rejected (see B7).

## B3 — Cancel login

Fresh launch -> close dialog (X-button path, WM_CLOSE) -> dialog closes,
session none. Subsequent Buy: REJECTED banner 165 -> 2,469 red pixels
(`08`-`10_*.png`). No bypass.

## B4 — Valid login

Fresh launch -> real secret -> Enter -> dialog closes, session visibly
established: statusbar `NOT LOGGED IN` -> `SESSION trader@TRADER`
(vision-read, `login_evidence_sheet.png`; pixel diff 14,927). Exactly one
Main Window, zero leftover LoginDialogs. Authenticated Buy keeps existing
behavior: green `Buy 0.1: APPLIED` banner (green pixels 2 -> 57,
`14_logged_in_after_buy.png`).

## B5 — Navigation semantic validation (9/9)

Per item: click (real mouse) -> visible workspace change (B-region pixel
diff 2,953-11,468; secondary evidence) AND semantic anchor: the workspace
title line, which encodes navigation + preset, matches the destination
reference exactly (reverse/repeat mismatches = 0 pixels). All 9 titles
pairwise distinct. Titles verbatim (vision-read, `nav_title_sheet.png`):

| Navigation | Visible B title (verbatim) |
|---|---|
| Overview | `B - Workspace : Overview [preset 'OVERVIEW']` |
| Markets | `B - Workspace : Markets [preset 'MARKET']` |
| Intelligence | `B - Workspace : Intelligence [preset 'INTELLIGENCE']` |
| Portfolio | `B - Workspace : Portfolio [preset 'RISK']` |
| Execution | `B - Workspace : Execution [preset 'EXECUTION']` |
| Research | `B - Workspace : Research [preset 'RESEARCH']` |
| Strategies | `B - Workspace : Strategies [preset 'RESEARCH']` |
| Risk | `B - Workspace : Risk [preset 'RISK']` |
| Operations | `B - Workspace : Operations [preset 'OPERATIONS']` |

Widget/model-level semantic state (navigation value, preset object, title
text, content text, no-exception) is asserted deterministically by
`tests/test_phase9_gui_repair.py` (15/15).

## B6 — Reverse / repeat navigation

Overview -> Research -> Execution -> Overview: title matches each
destination reference with **0 mismatch pixels** at every hop. Research
pressed 3x consecutively: titles identical across shots (0 diff), and after
the repeats Overview is reachable and matches its reference (0 mismatch) —
no duplicate widgets, no state corruption, no visual corruption, no
exception (stderr clean).

## B7/B8 — Rejected protected actions + banner lifecycle

Logged out, sequential real clicks:

| Action | red pixels before->after | banner text changed | red-text bbox height |
|---|---|---|---|
| Buy | 165 -> 2,469 | 5,758 px | 37 px |
| Pause | 2,469 -> 2,471 | 121 px | 37 px |
| Close Only | 2,471 -> 2,503 | 182 px | 37 px |
| Emergency | 2,503 -> 2,554 | 293 px | 37 px |

Banner verbatim (vision-read, `feedback_banner_sheet.png`):
`ACTION REJECTED — <Action>` / `Reason: Active authenticated session
required.` / `Next step: Log in first (persona: trader / risk_manager).`
Each action identified; constant 37px red-text height = exactly ONE 3-line
banner updating in place — no stacking/duplication.

## B9 — No side effect

Live: zero green/APPLIED pixels across all four rejected clicks; Overview
content after the four rejections (vision-read,
`overview_after_rejections.png`): `orders: 0`, `positions: 0`,
`session: NOT LOGGED IN`, `notifications: 4`. State-level no-side-effect
(0 orders, 0 positions, REJECTED receipts, no risk/authority change) is
asserted deterministically in `tests/test_phase9_gui_repair.py`.

## B10 — Ctrl+K

PASS with real keyboard input (`keybd_event`) after en-US layout
normalization; Command Palette window appears (`07_command_palette.png`),
Escape closes it. Binding untouched. Root cause of prior failures: Thai
keyboard layout -> Tk keysym `??` (see `KEYBOARD_LAYOUT_FINDING.md`).

## B11 — Existing GUI components

- **Search**: click search entry -> type `EURUSD` -> Enter -> search strip
  changes (732 px) + status/context updates (50 px).
- **Resize**: client 1280x800 -> 1084x741 -> restored 1280x800; workspace
  title identical (0 px diff) while resized and after restore.
- **Minimize/Restore**: IsIconic true -> restore -> title identical (0 px).
- **Close**: clean exit code 0.
- Inspector / Blotter / E-F panels are covered by the pytest suite
  (search -> inspector SYMBOL context -> E/F panes -> blotter row;
  `tests/test_phase9_gui_repair.py` TestExistingComponents) plus the
  authenticated-Buy blotter evidence in B4.

## B12 — Clean close / relaunch

Close -> exit code 0; zero zombie "1144 Trading OS" windows system-wide.
Relaunch -> exactly one Main Window + exactly one LoginDialog; fresh state
has no stale session (Buy on the fresh instance -> REJECTED 165 -> 2,469).

## PHASE C table (final)

```text
LoginDialog launch            PASS
Invalid Login                 PASS
Cancel Login                  PASS
Valid Login                   PASS
Navigation                    9/9 PASS
Reverse Navigation            PASS
Repeat Navigation             PASS
Buy rejection feedback        PASS
Pause rejection feedback      PASS
Close Only rejection feedback PASS
Emergency rejection feedback  PASS
Rejection no-side-effect      PASS
Existing GUI components       PASS
Ctrl+K                        PASS
Focus safety                  PASS
Clean close/relaunch          PASS
Runtime exceptions            0
```

Full machine-readable log: `live_gui_report.json` (every step, every
foreground check with timestamps, layout switches, per-instance exit codes).
