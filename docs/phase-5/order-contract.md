# Order Contract (Phase 5)

- **WHAT**: Order contract 1.1.0: execution lifecycle states (CREATED..UNKNOWN), causal chain fields (intent_id, portfolio_decision_id, risk_decision_hash, risk_context_hash, policy_version), idempotency_key, expires_at, order_version, provenance.
- **WHY**: An order is the execution object born ONLY from a valid risk decision - never from a signal (SECTION 3/4).
- **BOUNDARY**: Orders reference the intent->portfolio->risk chain; they never re-decide it.
- **SOURCE OF TRUTH**: architecture/schemas/order.yaml 1.1.0; machine architecture/state-machines.yaml#order_state 1.3.0.
- **INPUT**: OMS order construction from an authorized intent permission.
- **OUTPUT**: Immutable order versions (replacement bumps version).
- **FAILURE BEHAVIOR**: Orders without risk references never reach ACCEPTED; expired orders never submit.
- **RECOVERY**: Versions append-only; idempotency keys deduplicate semantic resubmissions.
- **AUDIT**: Every order transition audited.
- **TEST**: tests/test_phase5_boundary_oms.py
