# Walk Forward (Phase 6)

- **WHAT**: WalkForwardValidation over explicit windows with per-window dataset hash + parameter version.
- **WHY**: Advancing-window validation reveals period dependence (SECTION 62).
- **BOUNDARY**: Every window records its own dataset hash and parameter version.
- **SOURCE OF TRUTH**: core/research/pipeline.py:walk_forward
- **ASSUMPTIONS**: Windows are pre-declared, not chosen post-hoc.
- **FAILURE**: No windows -> FAILED.
- **RECOVERY**: n/a.
- **REPRODUCIBILITY**: Window records are content-addressed.
- **TEST**: TestValidation::test_walk_forward_windows
