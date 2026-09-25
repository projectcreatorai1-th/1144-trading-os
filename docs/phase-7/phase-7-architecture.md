# Phase 7 Architecture - Intelligence Layer

## Position in the system

DATA -> EVENT -> PIT DATASET -> FEATURE -> MODEL -> INFERENCE -> AI OUTPUT
-> AI PROPOSAL -> STRATEGY -> INTENT -> PORTFOLIO -> PROJECTED EXPOSURE
-> RISK ENGINE -> RISK DECISION -> OMS -> EMS -> MT5

The intelligence plane (core.intelligence) stops at AI PROPOSAL. Everything
from STRATEGY onward is the existing Phase 4/3/5 machinery.

## Module: core.intelligence (ACTIVE, architecture.yaml 1.7.0)

| file | responsibility |
|---|---|
| contracts.py | Feature/label/dataset/split/training/model/inference contracts |
| outputs.py | AIObservation/Prediction/Recommendation/Signal/Proposal, health, drift, OOD, explanation, replay, selection, card, incident |
| feature.py | deterministic PIT feature engine (visible_at reuse) + leakage audit |
| dataset.py | immutable intelligence datasets + split integrity |
| registry.py | content-addressed feature + model registries |
| adapter.py | ModelAdapter port + stdlib deterministic families |
| training.py | TrainingService with dependency lock |
| evaluation.py | metrics, calibration, stability, coverage |
| validation.py | model validation gate (critical evidence checklist) |
| inference.py | deterministic inference + AISafetyValidator (ONE gate) |
| explain.py | explainability + operational health |
| drift.py | drift engine + OOD detection |
| lifecycle.py | model lifecycle via Phase 0 machine (model_lifecycle) |
| proposal.py | output builders + Phase 6 StrategyCandidate factory |
| replay.py | read-only replay + diff + model comparison |
| events.py | Phase 7 event emission (contract 1.2.0) |

## Dependency boundary (one-way)

allowed: architecture.contracts, core.data, core.events, core.time,
core.validation, core.research (PIT datasets + StrategyCandidate reuse),
platform.audit, platform.security.

forbidden: core.oms, core.ems, core.execution, core.risk, core.policy,
core.portfolio, core.strategy, core.backtest, core.ledger, core.state,
core.reconciliation, adapters.* (mt5/broker/simulation), ui.*.

AI output reaches Strategy by DATA, not by import: the strategy plane
already depends on core.intelligence (Phase 4); intelligence never depends
back (no cycles; validator-enforced by IMPORT-001 + AI-001..AI-007).

## Registries extended (REUSE -> EXTEND -> VERSION)

- identifiers.yaml 1.6.0 -> 1.7.0 (+16 kinds)
- state-machines.yaml 1.3.0 -> 1.4.0 (+model_lifecycle, HUMAN_APPROVAL)
- schema-registry.yaml 1.6.0 -> 1.7.0 (+23 schemas generated FROM the
  python bindings - zero drift by construction)
- events contract 1.1.0 -> 1.2.0 (+13 advisory-plane event types)
- architecture.yaml 1.6.0 -> 1.7.0; manifest phase 7, system 0.8.0

No Phase 0-6 engine was duplicated or replaced.
