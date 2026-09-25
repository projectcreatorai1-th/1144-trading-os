# Portfolio Contract (Phase 4)

- **WHAT**: Portfolio + PortfolioMembership contracts.
- **WHY**: Versioned portfolio identity with explicit membership priority (SECTION 17/19).
- **SOURCE OF TRUTH**: schemas portfolio_record / portfolio_membership 1.0.0.
- **INPUT**: Contract fields.
- **OUTPUT**: Frozen validated instances.
- **IMMUTABILITY**: Versions unique; historical memberships never overwritten.
- **FAILURE**: Non-integer priority rejected; bool is not a priority.
- **RECOVERY**: Historical lookup via store.
- **VERSION**: 1.0.0.
- **TEST**: TestPortfolioContract
