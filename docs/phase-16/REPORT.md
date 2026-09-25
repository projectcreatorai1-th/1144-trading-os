# Phase 16 — UI Automation / UX Reliability — PASS

ui/desktop/ui_contract.yaml: semantic control registry — every critical
control declares control_id/accessible_name/role/action/expected_event/
expected_state_change/covered_by, cross-checked by tests against the REAL
collected suite (coverage cannot rot silently). All 13 critical workflows
mapped. Environment findings documented openly (Thai keyboard layout →
Tk keysym ??; DPI/resize verified live). INV-GUI-002 encoded as a test
(the four protective controls must declare banner feedback).
Tests: tests/test_phase16_ui_contract.py — 5/5.
