# Portfolio Exposure (Phase 4)

- **WHAT**: ExposureAggregator: gross/net/long/short + per symbol/strategy/direction/market + correlated groups.
- **WHY**: Cross-strategy exposure must be visible; per-strategy risk isolation is forbidden (SECTION 22/23).
- **SOURCE OF TRUTH**: core.portfolio.exposure (single aggregation point).
- **INPUT**: ExposureLegs (unsigned magnitude + direction; signs applied centrally).
- **OUTPUT**: PortfolioExposure (arithmetic-validated: gross = |long|+|short|).
- **IMMUTABILITY**: Frozen snapshot.
- **FAILURE**: Negative magnitudes rejected; correlated groups UNKNOWN without data.
- **RECOVERY**: Deterministic re-aggregation.
- **VERSION**: schema portfolio_exposure 1.0.0.
- **TEST**: TestExposure
