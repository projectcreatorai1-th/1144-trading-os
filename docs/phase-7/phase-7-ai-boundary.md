# AI Authority Boundary (SECTION 112)

AI is advisory/proposal intelligence. Final authority remains:
Policy -> Risk -> RiskDecision -> OMS -> EMS.

## AI MAY
analyze, classify, estimate, predict, detect, interpret, generate
evidence, generate recommendations, generate proposals, generate
StrategyCandidates (Phase 6 contract, DRAFT only).

## AI CANNOT (validator-enforced AI-001..AI-036)
- import or call OMS / EMS / broker / MT5 / simulation adapters (AI-001..003)
- create or import RiskDecision (AI-004) - one risk authority: core.risk
- bypass Policy / Portfolio / Strategy (AI-005..007)
- modify risk limits or safety policy (AI-008)
- self-promote / self-deploy / enable LIVE (AI-009..011)
- use non-PIT data (AI-012), drop provenance (AI-013)
- mutate feature versions silently (AI-029), use latest-anything
  (AI-026..028), interchange confidence/probability/score (AI-030)
- claim unsupported causality (AI-031), skip artifact validation (AI-032)
- mutate models in place (AI-033), write production state (AI-034)
- mutate LIVE in replay (AI-035), become an Order (AI-036)

## Semantic hierarchy (never interchangeable)

Observation, Prediction, Recommendation, Proposal, StrategyIntent, Order,
RiskDecision are each a separate frozen contract; an AIProposal
structurally has no order or risk field at all.

## Handover point

AI hands evidence to the existing flow: proposal -> StrategyCandidate
(Phase 6) -> Phase 4 strategy lifecycle (IDEA -> ... -> LIVE, human-gated)
-> Intent -> Portfolio -> Risk -> OMS. The candidate cannot skip states
and AI cannot touch the strategy registry.
