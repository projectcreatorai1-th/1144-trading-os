# Reconciliation (Phase 5)

- **WHAT**: Execution reconciliation through the Phase 2 engine: internal positions/executions vs broker observations.
- **WHY**: Mismatches are REPORTED, never auto-fixed; UNKNOWN stays UNKNOWN (SECTION 30).
- **BOUNDARY**: Reconciliation cannot mutate canonical state (INV-029).
- **SOURCE OF TRUTH**: core/reconciliation/engine.py (Phase 2) reused directly.
- **INPUT**: Internal projected state + broker observations.
- **OUTPUT**: MATCH/MISMATCH results with evidence.
- **FAILURE BEHAVIOR**: MISMATCH recorded; state untouched.
- **RECOVERY**: Post-reconnect polls feed observations for reconciliation.
- **AUDIT**: Mismatches audited by the Phase 2 engine.
- **TEST**: E2E-013 + INV-029
