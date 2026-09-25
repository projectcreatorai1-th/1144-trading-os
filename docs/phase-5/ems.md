# Ems (Phase 5)

- **WHAT**: ExecutionManagementSystem (core.ems): routing, adapter selection, capability validation, submission, timeout->UNKNOWN, retry classification, outbox.
- **WHY**: EMS = HOW a valid order is executed (SECTION 15/16).
- **BOUNDARY**: Accepts OMS-validated (ACCEPTED) orders only; never grants risk; never fabricates fills.
- **SOURCE OF TRUTH**: core/ems/engine.py + ExecutionAdapter port.
- **INPUT**: ACCEPTED order + risk decision + execution context.
- **OUTPUT**: SubmissionResult (ACKNOWLEDGED / REJECTED / UNKNOWN + broker reference + raw evidence).
- **FAILURE BEHAVIOR**: No adapter for environment -> fail closed (ENVX-001); timeout/connection/unknown outcome -> UNKNOWN; non-retryable broker error -> REJECTED.
- **RECOVERY**: poll() reconciles UNKNOWN; outbox preserves delivery intent.
- **AUDIT**: ORDER_SUBMITTED/EXECUTION_REJECTED/ORDER_POLLED audited with raw references.
- **TEST**: tests/test_phase5_ems_adapters.py
