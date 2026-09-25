# Strategy Lifecycle (Phase 4)

- **WHAT**: IDEA -> RESEARCH -> BACKTEST -> ROBUSTNESS -> OUT_OF_SAMPLE -> REPLAY -> PAPER -> DEMO -> FORWARD -> APPROVED -> LIVE (+SUSPENDED/RETIRED).
- **WHY**: No strategy reaches LIVE without the full promotion chain (SECTION 6).
- **SOURCE OF TRUTH**: architecture/state-machines.yaml#strategy_lifecycle (Phase 0 machine engine).
- **INPUT**: Registry transitions with actor permissions + injected timestamps.
- **OUTPUT**: New immutable versions + audited transitions.
- **IMMUTABILITY**: Every transition writes a patch-bumped version.
- **FAILURE**: Skipped lifecycle stages fail closed; unauthorized transitions rejected; LIVE requires APPROVED.
- **RECOVERY**: SUSPENDED -> LIVE requires policy approval context.
- **VERSION**: state-machines 1.2.0.
- **TEST**: TestRegistryLifecycle
