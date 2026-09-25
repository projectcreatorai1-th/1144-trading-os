# Phase 7 E2E Flows

22 executable flows (tests/test_phase7_invariants_e2e.py::TestE2EFlows):

- E2E-01 data -> PIT dataset -> feature -> inference (lineage verified)
- E2E-02 PIT violation -> BLOCK
- E2E-03 training -> evaluation -> validation gate
- E2E-04 training failure -> no validated model
- E2E-05 model V1 inference -> immutable evidence (idempotent)
- E2E-06 model V2 same data -> replay diff (human review, no winner)
- E2E-07 feature V1 -> V2 versioning (coexisting immutable versions)
- E2E-08 drift detection
- E2E-09 OOD detection
- E2E-10 news event -> interpretation -> AI proposal (advisory)
- E2E-11 proposal -> StrategyCandidate (Phase 6 contract, DRAFT)
- E2E-12 candidate -> existing Phase 4 lifecycle (no state skip)
- E2E-13 existing Strategy contract carries AI evidence in provenance
- E2E-14 risk BLOCK -> no Order (no order constructor exists)
- E2E-15 OMS boundary untouched (no OMS keys in provenance)
- E2E-16 direct order attempt -> architecture validation clean
- E2E-17 direct RiskDecision attempt -> unreachable
- E2E-18 promotion requires human evidence
- E2E-19 rollback references immutable version
- E2E-20 crash/restart/recovery (idempotent identity)
- E2E-21 training reproducibility (bit-identical artifacts)
- E2E-22 shadow mode advisory only
