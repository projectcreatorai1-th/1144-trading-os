# Phase 18 — Promotion Pipeline — PASS

core.strategy.promotion: PromotionEvidence (dataset/config/risk-profile
refs, performance metrics, failure-tests + regression gates, operator
approval, content hash) + PromotionLedger: strictly ordered
DRAFT→VALIDATED→SIMULATION→DEMO→SHADOW→CANARY→LIVE_CANDIDATE (skipping
refused, PRM-006), every record audited. LIVE-CANDIDATE is an evidence
state only — it enables nothing (the LIVE hard gate stays human).
Tests: tests/test_phase15_governance.py — 8/8 (shared suite).
