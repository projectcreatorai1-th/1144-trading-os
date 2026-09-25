# Phase 4 Portfolio (Phase 4)

- **WHAT**: Portfolio engine: contract, lifecycle machine, membership, capital allocation, risk budget allocation (reuse), exposure aggregation, correlation boundary, capacity, liquidity, constraints, decisions (core.portfolio).
- **WHY**: Portfolio manages strategy/capital/exposure allocation WITHOUT overriding risk or sending orders (SECTION 3).
- **SOURCE OF TRUTH**: Phase 0-3 foundations + portfolio documents/memberships in stores.
- **INPUT**: Portfolio + memberships + eligibility + exposure legs + capacities + liquidity.
- **OUTPUT**: PortfolioDecision (allocations, projected exposure, constraints, conflicts).
- **IMMUTABILITY**: Portfolio versions and memberships append-only.
- **FAILURE**: Non-ACTIVE portfolios reject allocation; ambiguous priority rejects; overflow rejects or explicitly constrains.
- **RECOVERY**: Restart-safe stores; deterministic engine.
- **VERSION**: portfolio_* schemas 1.0.0.
- **TEST**: tests/test_phase4_portfolio.py
