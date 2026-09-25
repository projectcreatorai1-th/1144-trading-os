# Phase 7 Contracts

23 new schemas (owner core.intelligence, all 1.0.0, BACKWARD):
feature_definition, feature_snapshot, intelligence_dataset,
label_definition, training_config, training_run, model_definition,
model_evaluation, model_validation, inference_request, inference_result,
ai_observation, ai_prediction, ai_recommendation, ai_proposal, ai_signal,
model_health, drift_report, explanation, model_replay, model_selection,
model_card, ai_incident.

## Key invariants encoded in the contracts

- FeatureDefinition: available_time_semantics mandatory (event-time-only
  visibility = LOOK_AHEAD); DECLARED_DEFAULT requires an explicit declared
  value; content_hash + implementation_hash lock definition and code.
- FeatureSnapshot: available_at <= as_of (PIT); content hash commits to
  availability (forged windows change identity).
- LabelDefinition: availability = event + horizon (TARGET_LEAKAGE guard).
- SplitDefinition: TRAIN < VALIDATION < OOS strictly increasing; declared
  overlap needs justification.
- IntelligenceDataset: content hash + normalization version + provenance
  to the source PIT dataset; research-plane environments only.
- TrainingConfig: no implicit defaults - hyperparameters, seed,
  thresholds, resource limits all explicit (AI-IMPLICIT on violation).
- TrainingRun: COMPLETED requires artifact + time; FAILED/INVALID require
  a reason; dependency_lock() captures the locked hashes.
- ModelDefinition: model_hash = content identity over artifact/code/
  features/dataset/label/config/evaluation/dependency/envs/seed/
  framework/runtime/status/version; explicit inference_environments only.
- ModelValidation: overall PRESENT forbidden while any critical evidence
  item is not PRESENT (MVAL-002).
- InferenceRequest: request_hash = idempotency identity.
- InferenceResult: confidence/probability/score are SEPARATE fields with
  [0,1] bounds (AI-SEMANTICS); provenance is tamper-bound into
  content_hash; explicit validity window.
- AIRecommendation: execution/risk verbs (BUY/SELL/SUBMIT_ORDER/
  CLOSE_POSITION/OVERRIDE_RISK/ENABLE_LIVE) rejected at the contract.
- AIProposal: evidence mandatory; direction enum includes UNKNOWN.
- Explanation: FEATURE_CONTRIBUTION must state non-causality; UNAVAILABLE
  explanations carry no fabricated evidence.
- ModelHealth: rates in [0,1]; no profitability fields exist.
- ModelSelection: selected must be a candidate; decision_actor recorded.
- AIIncident: evidence mandatory (auditable incidents).
