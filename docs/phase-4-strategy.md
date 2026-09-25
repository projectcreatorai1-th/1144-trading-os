# Phase 4 Strategy (Phase 4)

- **WHAT**: Strategy engine: contract, registry, lifecycle, capability, config, evaluation, intent, eligibility, kill criteria, health, dependency graph (core.strategy).
- **WHY**: Strategies propose trading INTENT under policy/risk authority - separated from execution (SECTION 0/3).
- **SOURCE OF TRUTH**: Phase 0-3 foundations (IDs/time/environment/state machines/policy/risk/audit) + strategy documents in the strategy store.
- **INPUT**: Strategy documents, capability profiles, configs, risk contexts, actor contexts.
- **OUTPUT**: Intents (never orders), evaluations, eligibility/kill/health verdicts.
- **IMMUTABILITY**: Strategy versions and configs are append-only; historical versions never edited.
- **FAILURE**: Missing/duplicate/ambiguous strategies, invalid lifecycle jumps, missing capability/policy/budget, unsupported symbol/environment, config hash mismatch - all fail closed.
- **RECOVERY**: Restart-safe stores; deterministic resolution and evaluation.
- **VERSION**: strategy_* schemas 1.0.0; state-machines registry 1.2.0; identifiers 1.4.0.
- **TEST**: tests/test_phase4_strategy.py
