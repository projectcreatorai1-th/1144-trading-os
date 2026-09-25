# Point In Time (Phase 6)

- **WHAT**: visible_at(moment) returns only observations whose available_time <= moment.
- **WHY**: Event at 10:00 available 10:02 is invisible at 10:01 (SECTION 9).
- **BOUNDARY**: Future-published data cannot enter past decisions.
- **SOURCE OF TRUTH**: core/research/contracts.py:ResearchDataset.visible_at
- **ASSUMPTIONS**: available_time >= event_time enforced at construction.
- **FAILURE**: Look-ahead consumption -> bias FAIL -> run INVALID.
- **RECOVERY**: n/a (structural).
- **REPRODUCIBILITY**: Pure function of the dataset.
- **TEST**: TestDataset::test_point_in_time_visibility
