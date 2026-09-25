# Dataset Contract (Phase 6)

- **WHAT**: ResearchDataset: immutable versioned point-in-time observations with content hash, lineage, quality summary and survivorship status.
- **WHY**: Research validity depends on what was knowable then (SECTION 7-8).
- **BOUNDARY**: Data changes create new versions, never edits.
- **SOURCE OF TRUTH**: core/research/contracts.py + Phase 1 quality/lineage reuse.
- **ASSUMPTIONS**: Observations carry event_time + available_time (availability >= event).
- **FAILURE**: Hash mismatch, missing timezone, research-plane environment violation -> rejected.
- **RECOVERY**: Rebuild from Phase 1 stores; content hash verified.
- **REPRODUCIBILITY**: Content hash commits to observations + symbols + range + timeframe.
- **TEST**: TestDataset
