# Portfolio Membership (Phase 4)

- **WHAT**: PortfolioMembership: strategy@version + allocation + risk budget + EXPLICIT integer priority + effective window.
- **WHY**: Historical membership is truth; priorities must never be implicit (SECTION 19/32).
- **SOURCE OF TRUTH**: schema portfolio_membership 1.0.0.
- **INPUT**: Membership records.
- **OUTPUT**: Membership inputs to allocation.
- **IMMUTABILITY**: Append-only; changes create new records.
- **FAILURE**: Duplicate priorities among active members rejected (PORTFOLIO-002).
- **RECOVERY**: Membership history queryable.
- **VERSION**: 1.0.0.
- **TEST**: TestPortfolioContract + allocation tests
