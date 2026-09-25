# Strategy Capability (Phase 4)

- **WHAT**: CapabilityProfile: strategy type, supported symbols/markets/environments, position/grid/lot/exposure/risk ceilings, sensitivities, liquidity/margin requirements, capacity estimate.
- **WHY**: Eligibility needs declared capabilities; observations must be distinguishable from model assumptions (SECTION 8).
- **SOURCE OF TRUTH**: schema strategy_capability 1.0.0; provenance REQUIRED on every profile.
- **INPUT**: Declared/observed capability data + provenance.
- **OUTPUT**: support checks (symbol/environment) used by eligibility.
- **IMMUTABILITY**: Frozen dataclass; stored by id.
- **FAILURE**: Missing provenance fails closed (STRATEGY-004).
- **RECOVERY**: Profiles re-readable after restart.
- **VERSION**: 1.0.0.
- **TEST**: TestCapabilityAndConfig
