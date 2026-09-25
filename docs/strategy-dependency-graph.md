# Strategy Dependency Graph (Phase 4)

- **WHAT**: DependencyGraph: strategy -> data source/feature/event/policy/risk/portfolio edges with impact queries and cycle rejection.
- **WHY**: 'If data source X breaks, which strategies are affected?' must be answerable (SECTION 16).
- **SOURCE OF TRUTH**: core.strategy.dependency.DependencyGraph (analysis structure only).
- **INPUT**: Declared dependency edges.
- **OUTPUT**: dependencies_of / impacted_by queries.
- **IMMUTABILITY**: Graph is runtime-rebuilt from declarations (not an execution graph).
- **FAILURE**: Cycles fail closed (DependencyCycleError, STRATEGY-003).
- **RECOVERY**: Graph rebuilt deterministically on restart.
- **VERSION**: 1.0.0.
- **TEST**: TestDependencyGraph
