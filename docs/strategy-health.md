# Strategy Health (Phase 4)

- **WHAT**: StrategyHealth: operational/data/risk/lifecycle states, health status, failure/anomaly counts, staleness, capacity status.
- **WHY**: Health is operational - never judged by a single performance metric (SECTION 15).
- **SOURCE OF TRUTH**: schema strategy_health 1.0.0.
- **INPUT**: Observed operational facts.
- **OUTPUT**: HEALTHY / DEGRADED / UNHEALTHY / UNKNOWN.
- **IMMUTABILITY**: Frozen snapshot records.
- **FAILURE**: UNKNOWN is explicit.
- **RECOVERY**: Health recomputed from facts on demand.
- **VERSION**: 1.0.0.
- **TEST**: TestKillAndHealth
