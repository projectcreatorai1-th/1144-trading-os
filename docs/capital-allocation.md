# Capital Allocation (Phase 4)

- **WHAT**: CapitalAllocator: centralized arithmetic (available = total - reserved; allocated = sum; remaining = available - allocated) with explicit overflow behavior.
- **WHY**: No strategy computes its own capital; overflow is never silently clipped (SECTION 20).
- **SOURCE OF TRUTH**: core.portfolio.allocation (the only place capital arithmetic lives).
- **INPUT**: Total/reserved capital, memberships (priority-ordered), requests.
- **OUTPUT**: CapitalAllocation (arithmetic self-validated).
- **IMMUTABILITY**: Frozen result stored append-only.
- **FAILURE**: Overflow REJECTs or explicitly CONSTRAINs; ambiguous priority fails closed; negative requests rejected.
- **RECOVERY**: Deterministic reallocation on restart.
- **VERSION**: schema capital_allocation 1.0.0.
- **TEST**: TestCapitalAllocation
