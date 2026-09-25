# Environment Model

- **WHAT**: Explicit environment enum for everything that can affect execution: RESEARCH, SIMULATION, REPLAY, PAPER, DEMO, LIVE.
- **WHY**: A simulation order must never reach a live broker; environment mixing is the classic trading-system failure (RULE 012).
- **BOUNDARY**: Environments live in `architecture.yaml` (single source), mirrored by the environment state machine, the manifest, permission restrictions and the `Environment` enum in the kernel. Tests keep them identical.

## Contract rules

1. Every order/decision/action that affects execution MUST carry an environment; missing or unknown values are hard failures (`ENV-002`), not defaults.
2. Two environments that must match (order vs risk decision, order vs execution target, adapter vs runtime) are compared with `assert_same_environment` - mismatch fails closed (`ENV-001`).
3. Environment changes are transitions in the `environment` state machine: adjacent steps only (RESEARCH <-> SIMULATION <-> REPLAY <-> PAPER <-> DEMO <-> LIVE). Skipping (e.g. RESEARCH -> LIVE) is invalid.
4. Permission grants are environment-scoped (e.g. LIVE_TRADE only in LIVE, SIMULATE never in LIVE).

## Fail-closed examples (tested)

- order(environment=PAPER) submitted with risk decision(environment=LIVE) -> blocked
- order(environment=SIMULATION) sent to LIVE execution target -> blocked
- `parse_environment("PRODUCTION")` / `parse_environment(None)` -> error

- **INPUT**: environment values on contracts.
- **OUTPUT**: validated environments or structured errors.
- **DEPENDENCY**: kernel environment module + state machine registry.
- **FAILURE**: deterministic, closed - never falls back to a default environment.
- **VERSION**: contract 1.0.0.
- **TEST**: `tests/test_environment.py`, `tests/test_order.py` (environment mismatch cases), `tests/test_permission.py`.
