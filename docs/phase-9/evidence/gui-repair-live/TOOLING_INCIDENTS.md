# Tooling / Environment Incidents (audit trail)

Three environment incidents occurred during this repair. None changed any
production file; all are documented so the evidence chain stays honest.

## Incident 3 — disk full (0 bytes free), resolved

The first gate rerun of the final-validation command failed (D4 full pytest
exit 1 with `ERROR at setup`, and gates E/F/G1/G2 exiting immediately with
code 2). Root cause: `C:` had **0 bytes free** (312.9 GB used). The
validator test suite's `project_copy` fixture copies the ENTIRE project into
`%TEMP%\pytest-of-BANK` for every test, which had accumulated **9.0 GB**, and
pip cache held another 4.2 GB.

Cleanup (safe-by-definition artifacts only, created by this session's test
activity): deleted `%TEMP%\pytest-of-BANK` (9 GB) and ran `pip cache purge`
(4.2 GB) -> 13 GB free. Gates were then rerun from scratch. The full report
distinguishes the failed (disk-full) gate run from the post-cleanup rerun.

## Incident 1 — host process-creation failure (earlier session, resolved)

Immediately after the first ALL-PASS live validation (driver v3 era), the
session's process-creation layer stopped working for ~10 minutes: Bash
returned empty output for every command (including `echo`), computer-use
`open_application` timed out ("queue operation timeout"), `list_apps`
returned `[]`, and a subagent's shell was silent too. File Read/Write kept
working. The host later reported the cause: silent failures were triggered
by a pending internet-permission prompt attached to sandboxed echo commands.
Recovery: shell returned; all gates were then run and passed. A previous
version of this note (TOOLING_BLOCKED.md) was written during the outage.

## Incident 2 — external file deletion + test-suite restructure (this session)

Between two turns of this session, files were modified OUTSIDE the repair
workflow (likely related to the same storage pressure / an external cleanup):

- deleted: `tools/p9r_gates.py` (gate runner),
  `docs/phase-9/evidence/gui-repair-live/regression_gates.json`,
  `BASELINE_REPAIR_RECORD.json`, `KEYBOARD_LAYOUT_FINDING.md`,
  `LIVE_VALIDATION_REPORT.md`, `TOOLING_BLOCKED.md`, `SUMMARY.txt`,
  `docs/phase-9/PHASE_9_GUI_BASELINE_REPAIR.md`
- test files externally restructured:
  `test_phase9_desktop_core.py` + `test_phase9_shell.py` ->
  `tests/test_phase9_core.py` (32 tests);
  `test_phase9_e2e.py` -> `tests/test_phase9_invariants_e2e.py` (22 tests)
- ALSO modified externally (found by the Phase H manifest diff, NOT part of
  this repair): `architecture/architecture.yaml`, `identifiers.yaml`,
  `manifest.yaml`, `schema-registry.yaml`, `state-machines.yaml`,
  `architecture/validator/rules.py`, `core/events/contracts.py`,
  `platform/api/desktop_gateway.py`
- untouched and verified intact: production fixes
  (`ui/desktop/shell.py`, `ui/desktop/run_windows.py`),
  `tests/test_phase9_gui_repair.py` (15 tests), the original
  `docs/phase-9/PHASE_9_FILE_HASHES.json`, and all PNG evidence.

Response: the gate runner was recreated against the ACTUAL test files (the
restructure is reported in `regression_gates.json`, not hidden), the
evidence documents were regenerated from the current driver-v4 data, and the
baseline record flags the 8 out-of-scope modifications explicitly. The
driver v4 rerun in this session passed the full PHASE C table, proving the
production GUI repair was not affected. The regression gates remain the
authority on whether the externally-modified files are self-consistent.
