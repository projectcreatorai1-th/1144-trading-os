# Phase 7 Benchmark

**SYNTHETIC / LOCAL / NON-PRODUCTION**

- dataset: 60 SYNTHETIC observations, 2 features, 30 training rows
- model: CLASSIFIER (stdlib deterministic logistic) (CPU, single process, no GPU)
- iterations per measurement: 30

| operation | p50 ms | p95 ms | p99 ms | max ms |
|---|---|---|---|---|
| feature_generation | 0.039 | 0.044 | 0.052 | 0.052 |
| dataset_generation_38_rows | 1.491 | 2.091 | 2.195 | 2.195 |
| inference_cold_engine | 0.106 | 0.255 | 0.258 | 0.258 |
| evaluation_30_rows | 0.29 | 0.406 | 0.495 | 0.495 |
| explanation | 0.019 | 0.032 | 0.042 | 0.042 |
| drift_check | 0.017 | 0.023 | 0.033 | 0.033 |
| replay_10_moments | 0.511 | 0.892 | 1.374 | 1.374 |

## Notes

- Deterministic stdlib model families; no external ML framework.
- Numbers measure this machine's Python interpreter only.
- Never interpret as production throughput.
- Raw numbers: phase-7-benchmark.json (same directory).
