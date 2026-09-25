# Execution Adapter (Phase 5)

- **WHAT**: ExecutionAdapter port (connect/disconnect/health/capabilities/submit/cancel/replace/poll) + AdapterCapability with provenance.
- **WHY**: Core must never bind to MT5; adapters implement the boundary (SECTION 17).
- **BOUNDARY**: UNKNOWN capability (e.g. position semantics) blocks execution (INV-032).
- **SOURCE OF TRUTH**: core/ems/adapter.py (port); capabilities carry provenance and version.
- **INPUT**: Canonical request mappings.
- **OUTPUT**: AdapterResponse (normalized error taxonomy + preserved raw evidence).
- **FAILURE BEHAVIOR**: UNKNOWN capability -> REJECT; unsupported symbol/type/TIF -> REJECT.
- **RECOVERY**: Connection state (CONNECTED/DISCONNECTED/CONNECTING/DEGRADED/UNKNOWN) never implies order state.
- **AUDIT**: Raw broker responses preserved in every report's provenance.
- **TEST**: tests/test_phase5_ems_adapters.py
