# Phase 8 Test Report

Suites (all green, measured):
- tests/test_phase8_core.py - 72 unit tests (authentication, sessions,
  credentials, secrets, keys, authorization, approvals, replay/rate,
  audit chain incl. 8 parametrized tamper mutations, backup, incidents,
  events, governance gate)
- tests/test_phase8_failures.py - 27 failure-matrix rows
- tests/test_phase8_invariants_e2e.py - 55 tests: invariants INV-086..
  INV-116 + 45 E2E scenarios (SECTION 44 coverage: authentication
  success/failure, credential expiry/revocation, session lifecycle,
  role resolution, (un)authorized operations, maker-checker, self-
  approval rejection, LIVE permission, DEMO->LIVE isolation, permission
  revocation, privilege escalation/actor spoofing/approver spoofing,
  replay + idempotency, rate limiting, secret redaction + persistence
  rejection, credential rotation, audit generation/verification/tamper,
  config + policy + risk + strategy + model governance, AI self-approval/
  deployment rejection, emergency stop + release governance, backup
  create/verify/corrupt/restore, incident evidence, full chain)
- tests/test_phase8_validator.py - 68 tests: positive (real project
  passes; all 60 SEC rules registered; 216 total rules) + corruption for
  every rule + phase-boundary checks.

Exact counts are in RELEASE_REPORT (never estimated).
