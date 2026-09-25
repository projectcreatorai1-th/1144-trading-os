# Model Lifecycle (SECTION 41)

Machine model_lifecycle (state-machines.yaml 1.4.0), driven through the
Phase 0 StateMachineRegistry - no second state engine.

DRAFT -> TRAINED -> VALIDATED -> OOS_VALIDATED -> RESEARCH_APPROVED
[HUMAN_APPROVAL] -> SHADOW [HUMAN_APPROVAL] -> PAPER [HUMAN_APPROVAL]
-> DEMO [HUMAN_APPROVAL] -> PRODUCTION_ELIGIBLE [HUMAN_APPROVED chain]
-> RETIRED

Failure paths: DRAFT->FAILED, TRAINED/VALIDATED->INVALID,
OOS_VALIDATED/RESEARCH_APPROVED->REJECTED, SHADOW/PAPER/DEMO/
PRODUCTION_ELIGIBLE->SUSPENDED, SUSPENDED->SHADOW [HUMAN_APPROVAL] or
RETIRED; terminal RETIRED.

## Rules
- Skipping states is a machine error (tested: DRAFT->PRODUCTION_ELIGIBLE
  rejected).
- Promotion targets additionally require Permission.APPROVE (VIEWER
  cannot promote - tested).
- Status is part of model content identity: every transition registers a
  NEW immutable patch version; historical versions stay queryable
  (rollback = referencing an earlier version, INV-082).
- Retirement never deletes evidence; historical inference stays valid.
- Training failures never create models at all.

## Environment approval
inference_environments is an explicit tuple; Phase 7 ships models
approved for RESEARCH only. LIVE is not assignable by the AI plane
(AI-011) and the safety gate blocks LIVE below PRODUCTION_ELIGIBLE.
