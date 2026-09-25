# Intelligence Replay + Diff (SECTION 44/45/82/83)

IntelligenceReplayAdapter.replay() re-derives feature snapshots and
inference over historical moments in the REPLAY environment (read-only;
the dataset hash is asserted unchanged). ModelReplay records original vs
reproduced output hashes: MATCH or MISMATCH with per-index differences.

## Determinism

Same dataset + feature versions + model version + config + seed ->
same outputs. The built-in model families are closed-form or
fixed-schedule deterministic: bit-identical artifacts (tested by
test_e2e21_training_reproducibility - two independent trainings produce
identical model hashes). No framework nondeterminism exists to declare.

## Diff engine (SECTION 45)

diff_inferences classifies every difference:
- different model hashes -> EXPECTED_VERSION_DIFFERENCE
- same model hash, different outputs -> NONDETERMINISM_SUSPECTED

compare_models produces model regression evidence (V1 vs V2) with the
explicit decision "HUMAN_REVIEW_REQUIRED (no automatic winner)".
