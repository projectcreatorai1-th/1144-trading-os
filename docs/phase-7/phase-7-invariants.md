# Phase 7 Invariants (INV-061..INV-085)

25 executable groups (tests/test_phase7_invariants_e2e.py::TestInvariants):

- INV-061 features cannot see unavailable data
- INV-062 feature versions immutable
- INV-063 intelligence datasets immutable
- INV-064 train/OOS separation enforced
- INV-065 model hash integrity
- INV-066 model dependency lock recorded and consistent
- INV-067 model/snapshot schema compatibility
- INV-068 failed training cannot validate
- INV-069 invalid model cannot infer (safety gate BLOCK)
- INV-070 inference provenance complete
- INV-071 inference environment match
- INV-072 expired model blocked
- INV-073 expired inference has explicit window
- INV-074 critical UNKNOWN blocks
- INV-075 AI cannot create RiskDecision
- INV-076 AI cannot create Order
- INV-077 AI cannot bypass Strategy
- INV-078 AI cannot bypass Portfolio
- INV-079 AI cannot bypass Risk
- INV-080 AI cannot self-promote
- INV-081 production model immutable
- INV-082 rollback references immutable version
- INV-083 replay is read-only
- INV-084 AI output is auditable
- INV-085 explanation provenance valid
