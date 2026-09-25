# Phase 7 Architecture Validator Rules

36 new rules (AI-001..AI-036) + updated BOUNDX-001; validator total 156
rules. Every rule has a positive test (real project passes) and a
corruption test (deliberate violating fixture caught) -
tests/test_phase7_validator.py (43 tests).

AI-001..003 no OMS/EMS/broker imports; AI-004 no RiskDecision; AI-005..007
no policy/portfolio/strategy bypass; AI-008 no risk-limit mutation;
AI-009 human approval + permission in lifecycle; AI-010 no self-deploy;
AI-011 no LIVE enablement; AI-012 PIT oracle (visible_at); AI-013
provenance; AI-014 SemVer; AI-015 artifact hash; AI-016 feature schema
hash; AI-017 immutable content-hashed datasets; AI-018 environment gate;
AI-019 BLOCK semantics + PIT enforcement; AI-020..024 no second
risk/state/event-store/ledger/replay engine; AI-025 no strategy lifecycle
mutation; AI-026..028 no latest/fallback model or dataset; AI-029
immutable feature registration; AI-030 separated confidence/probability/
score declarations; AI-031 non-causal explanations; AI-032 artifact
integrity validation; AI-033 frozen ModelDefinition; AI-034 no production
state writes; AI-035 replay read-only; AI-036 no Order construction.

BOUNDX-001 now blocks Phase 8 scope (governance expansion, desktop GUI,
web workspace) and AI-authority modules outside core.intelligence.
