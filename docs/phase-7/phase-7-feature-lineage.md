# Feature Lineage (SECTION 54/55)

RAW -> NORMALIZED -> EVENT -> PIT DATASET -> FEATURE -> INTELLIGENCE
DATASET -> MODEL -> INFERENCE -> AI OUTPUT -> PROPOSAL -> CANDIDATE

Every link is a verified content hash:

- Phase 1 ingestion produces normalized events with lineage (unchanged).
- Phase 6 ResearchDataset commits observations + availability to
  content_hash (unchanged).
- FeatureDefinition.content_hash commits the definition;
  implementation_hash commits the code (registration refuses divergence).
- FeatureSnapshot.content_hash commits values + as_of + AVAILABLE_AT.
- IntelligenceDataset.content_hash commits rows (row_digest) + feature
  schema hash + source dataset hash + normalization version.
- TrainingRun.dependency_lock() locks dataset/feature/label/config/code/
  dependency hashes at start; verified at completion.
- ModelDefinition.model_hash commits every immutable input incl. the
  artifact hash; integrity re-verifiable (verify_artifact_integrity).
- InferenceResult.content_hash commits output + confidence/probability/
  score/uncertainty + THE FULL PROVENANCE (tamper-evident).

Broken provenance (missing or lying) is a contract error or content
mismatch - never silently accepted (tested: failures + INV-070).

## Point-in-time rule (SECTION 6/24)

The ONLY availability oracle is ResearchDataset.visible_at (Phase 6).
Feature snapshots call it; the inference engine independently rejects any
snapshot whose available_at exceeds inference_time
(POINT_IN_TIME_VIOLATION); verify_snapshot_pit re-derives visibility as
defense-in-depth. There is no second PIT implementation.
