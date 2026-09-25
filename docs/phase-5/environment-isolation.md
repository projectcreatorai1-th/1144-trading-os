# Environment Isolation (Phase 5)

- **WHAT**: SIMULATION / PAPER / DEMO / LIVE adapter binding with per-environment adapters; cross-environment submission structurally impossible.
- **WHY**: A DEMO order must never reach a LIVE adapter (SECTION 19; E2E-014 matrix).
- **BOUNDARY**: EMS resolves adapters BY environment; missing adapter -> fail closed (never falls back).
- **SOURCE OF TRUTH**: core/ems/engine.py._adapter_for + Environment contract (Phase 0).
- **INPUT**: Order environment.
- **OUTPUT**: Same-environment adapter or hard failure.
- **FAILURE BEHAVIOR**: Cross-environment -> REJECT (tested for all 6 pairs).
- **RECOVERY**: n/a (structural).
- **AUDIT**: Routes audited with environment.
- **TEST**: TestEnvironmentIsolation + E2E-014
