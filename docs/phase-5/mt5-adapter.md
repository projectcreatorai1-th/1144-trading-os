# Mt5 Adapter (Phase 5)

- **WHAT**: MT5ExecutionAdapter: canonical->MT5 request mapping, MT5 response->normalized AdapterResponse; MT5Transport port with deployment-activated MetaTrader5 implementation.
- **WHY**: MT5 semantics stay in the adapter; the domain never sees MT5 objects (SECTION 18; validator MT5-001/002).
- **BOUNDARY**: Binds explicitly to DEMO or LIVE (never SIMULATION/PAPER); credentials live in the deployment secret store, never in source.
- **SOURCE OF TRUTH**: adapters/mt5/execution.py + adapters/mt5/transport.py.
- **INPUT**: Canonical requests from the EMS.
- **OUTPUT**: Normalized responses with raw retcodes preserved.
- **FAILURE BEHAVIOR**: MT5 retcodes normalized to the canonical taxonomy (10014->INVALID_VOLUME etc.); unmapped codes -> UNKNOWN.
- **RECOVERY**: poll() queries broker state for reconciliation.
- **AUDIT**: Raw evidence in every response.
- **TEST**: tests/test_phase5_ems_adapters.py (TestMT5Mapping)
