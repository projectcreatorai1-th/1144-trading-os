# Strategy Intent (Phase 4)

- **WHAT**: StrategyIntent (OPEN/INCREASE/REDUCE/CLOSE/HOLD/CANCEL_INTENT): proposed action with quantity/direction/conditions/urgency/rationale and full causal reference chain.
- **WHY**: INTENT != SIGNAL != ORDER (SECTION 11): intents propose; risk permits; orders are Phase 5.
- **SOURCE OF TRUTH**: schema strategy_intent 1.0.0.
- **INPUT**: Strategy evaluation outputs.
- **OUTPUT**: Intents eligible for the portfolio->risk gate.
- **IMMUTABILITY**: Frozen; intents stored append-only with unique ids.
- **FAILURE**: Expired intents invalid; FLAT cannot increase risk; zero-quantity OPEN rejected; floats rejected.
- **RECOVERY**: Duplicate intent ids rejected by the store (canonical uniqueness).
- **VERSION**: 1.0.0.
- **TEST**: TestIntent
