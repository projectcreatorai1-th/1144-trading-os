# Portfolio Lifecycle (Phase 4)

- **WHAT**: DRAFT -> REVIEW -> APPROVED -> ACTIVE -> PAUSED/SUSPENDED -> RETIRED (machine portfolio_status).
- **WHY**: Portfolios activate only after approval; pauses stop allocation (SECTION 18).
- **SOURCE OF TRUTH**: architecture/state-machines.yaml#portfolio_status.
- **INPUT**: Permission-controlled transitions.
- **OUTPUT**: New portfolio versions.
- **IMMUTABILITY**: Append-only versions.
- **FAILURE**: Invalid transitions fail closed; non-ACTIVE blocks decisions (PORTFOLIO-005).
- **RECOVERY**: Resume via approved transitions.
- **VERSION**: state-machines 1.2.0.
- **TEST**: decision tests + validator corruption tests
