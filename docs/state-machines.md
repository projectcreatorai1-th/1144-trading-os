# State Machines

- **WHAT**: Registry of every state machine: valid states, valid transitions, invalid transitions, transition guards.
- **WHY**: State must never change via arbitrary strings; every change is declared, validated and recorded (SECTION 10, RULE 017).
- **BOUNDARY**: The registry (`architecture/state-machines.yaml`) is the definition; `architecture.contracts.state_machine` is the engine. Phase 0 has no long-running stateful services - the engine validates transitions on demand.

## Machines

| Machine | States | Notes |
|---|---|---|
| system_state | STARTING, READY, RUNNING, DEGRADED, SAFE_MODE, STOPPED | STOPPED terminal |
| market_state | CALM, NORMAL, VOLATILE, EXTREME, UNKNOWN | UNKNOWN is first-class; moves at most one step, or to/from UNKNOWN |
| risk_state | NORMAL, CAUTION, LIMITED, PAUSE, EMERGENCY | escalation may jump; de-escalation is stepwise only |
| execution_state | READY, DEGRADED, BLOCKED, CLOSE_ONLY, DISCONNECTED | any state may disconnect |
| order_state | SIGNAL, DECISION, ORDER_CREATED, RISK_CHECK, SUBMITTED, ACCEPTED, PARTIAL_FILL, FILLED, CANCELLED, REJECTED, CLOSED | RISK_CHECK -> SUBMITTED requires `VALID_RISK_DECISION` |
| position_status | OPEN, PARTIALLY_CLOSED, CLOSED | CLOSED sets quantity to 0 |
| environment | RESEARCH, SIMULATION, REPLAY, PAPER, DEMO, LIVE | adjacent promotion/demotion only |
| policy_status | DRAFT, ACTIVE, SUSPENDED, RETIRED | activation requires `POLICY_APPROVED` |
| decision_status | PROPOSED, VALIDATED, EXECUTED, REJECTED, EXPIRED | |

## Transition contract

`StateMachineRegistry.apply(machine, current, target, reason=..., actor=..., context=..., timestamp=...)`
returns a `TransitionRecord` (machine, previous_state, new_state, reason, actor, timestamp, requirement, correlation_id) or raises:

- `UnknownStateError` (SM-002) - state not in the machine
- `StateTransitionError` (SM-001) - transition not declared
- `RequirementNotMetError` (SM-004) - guard not satisfied (e.g. risk decision missing/expired/blocked)
- missing reason or actor - rejected (transitions must be explainable)

Guard context keys: `risk_decision_validated` (set only by the order contract after checking the `RiskDecision`), `policy_approved` (set after approval verification).

- **INPUT**: current/target states + reason/actor/context.
- **OUTPUT**: TransitionRecord or structured error.
- **DEPENDENCY**: kernel registry loader.
- **FAILURE**: every invalid attempt fails deterministically; the engine never guesses a "closest" state.
- **VERSION**: state-machines registry 1.0.0.
- **TEST**: `tests/test_state_machine.py`, `tests/test_order.py`, `tests/test_policy.py`, `tests/test_environment.py`.
