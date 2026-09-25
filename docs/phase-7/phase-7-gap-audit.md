# Phase 7 Gap Audit

## Deliberately deferred (NOT critical gaps)

- GAP-020 Real ML frameworks (gradient-boosted trees, deep nets): Phase 7
  ships deterministic stdlib model families behind the ModelAdapter port.
  The adapter boundary is the explicit contract for adding frameworks
  later; no performance is fabricated. The advisory boundary and the
  PIT/leakage/safety machinery are framework-independent and fully
  exercised.
- GAP-021 Ensemble family: the registry supports the ENSEMBLE family
  name; the built-in adapter set ships BASELINE/LINEAR/CLASSIFIER/ANOMALY/
  REGIME. Ensemble composition rules (weights/voting/conflict, SECTION 60)
  slot into ModelAdapter + ModelSelection.
- GAP-022 NLP/text adapter (SECTION 33): news flows through the data plane
  as observations (E2E-10 proves the flow); a dedicated NLP model adapter
  is framework work (see GAP-020).
- GAP-023 Online learning hooks (SECTION 86): deliberately NOT implemented
  for production models (forbidden); the research plane IS the
  research-only observation pipeline.
- GAP-024 Multi-model serving, GPU runtime, distributed training:
  infrastructure concerns beyond the advisory plane's contracts.

## Critical gaps: 0
