# Phase 7 Security

## Threat model coverage (SECTION 98)

| threat | control |
|---|---|
| malicious model artifact | artifact JSON parsed (never pickle); inner artifact_hash + outer artifact_hash verified on load (ADAPTER-003/004); mismatch = UNKNOWN integrity |
| malformed metadata | raise-on-invalid frozen dataclasses everywhere |
| path traversal | artifacts are in-memory canonical JSON; no caller file paths |
| unsafe deserialization | no pickle/eval/exec in core.intelligence (static audit); json-only artifacts |
| oversized data | resource_limits mandatory in TrainingConfig |
| resource exhaustion | contract-enforced limits (AI-IMPLICIT); no hidden retry loops |
| malicious feature definition | definition+implementation hash pairing; registry rejects content drift |
| schema poisoning | schemas generated from bindings; drift test enforces equality |
| provenance forgery | provenance bound into InferenceResult.content_hash; validation requires provenance PRESENT |
| hash forgery | every content hash recomputed on validate() |
| unauthorized promotion | model_lifecycle machine + HUMAN_APPROVAL + APPROVE permission (AI-009, E2E-18) |
| unauthorized LIVE inference | LIVE not assignable in inference_environments (AI-011); safety gate double-blocks |

## Secrets

No credentials anywhere in the intelligence plane (static scan clean);
audit records carry ids/hashes only. SECX-001 unchanged.

## Network

No network calls inside core inference (SECTION 119) - static audit
clean. External providers (news etc.) enter as data through the Phase 1
pipeline.
