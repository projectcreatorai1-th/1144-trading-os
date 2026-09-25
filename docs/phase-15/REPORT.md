# Phase 15 — Configuration Governance — PASS

core.policy.config_governance: GovernedConfig (config_id/version/kind/
content hash/status/created_by/approved_by/effective window) +
ConfigGovernor: audited lifecycle CREATE→VALIDATE→REVIEW→APPROVE→ACTIVATE
(+ explicit ROLLED_BACK), content SHA-256 pinning, and active_at(t)
historical reconstruction ("which version governed this at transaction
time?" answered from effective windows, never memory).
Tests (with 15B/18): tests/test_phase15_governance.py — 8/8.
