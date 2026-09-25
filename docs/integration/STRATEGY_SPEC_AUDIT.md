# STRATEGY_SPEC_AUDIT.md — Pre-Market Phase 1 (2026-09-26)

Source of truth audited: SNIPER-CashFlow-Analyzer `STRATEGY_SPEC/`
(tag `spec/1.0.0` → 175a259; spec hash 4648DFBB…; evidence model
124F0898… per COMPATIBILITY_MATRIX). Statuses below come from the spec's
own `status` fields — nothing was invented or upgraded.

## Classification (spec's own evidence statuses)

| # | Item | Status | Notes / blocker for DEFINED |
|---|---|---|---|
| 1 | Entry — initial | **DEFINED (VERIFIED)** | condition+action+evidence_refs |
| 2 | Entry — grid re-entry (partial entry) | **DEFINED (VERIFIED)** | grid add condition verified |
| 3 | Entry — restart | **UNKNOWN** | spec marks restart-entry UNKNOWN — needs SNIPER research on restart behavior; blocker: no observed restart evidence in dataset |
| 4 | Exit — basket close | **PARTIAL** | mechanism verified, exact close trigger PARTIAL |
| 5 | Exit — partial close | **PARTIAL** | existence verified, trigger UNKNOWN (spec's own note) |
| 6 | Exit — emergency close | **UNKNOWN** | mechanism not evidence-verified |
| 7 | Basket close trigger | **PARTIAL** | compatible hypotheses listed, not isolated |
| 8 | Grid anchor | **PARTIAL** | hypotheses_indistinguishable in spec |
| 9 | Grid trigger/spacing | **DEFINED (VERIFIED)** | nominal+unit+distribution verified |
| 10 | Grid direction | **DEFINED** | structured (spec-consistent) |
| 11 | Signal — entry | **DEFINED (VERIFIED)** | |
| 12 | Signal — grid add | **PARTIAL** | depends on grid-anchor hypothesis |
| 13 | Signal — exit | **PARTIAL** | |
| 14 | Lot model (base/multiplier/step/rounding/overflow) | **DEFINED** | formula + rounding + observed range |
| 15 | Risk rules (invariants INV-001…) | **DEFINED** | invariant_spec.json (enforced in EA) |
| 16 | Uncertainty / UNKNOWN policy | **DEFINED** | default_behavior + unknown_rules (fail-closed) |
| 17 | Position management | **PARTIAL** | via basket/grid/partial models; no standalone doc |
| 18 | Session rules (hours/weekend/rollover) | **UNKNOWN** | no `session` content anywhere in spec — needs SNIPER research input |
| 19 | Recovery rules | **PARTIAL** | `recovery`/`restart` mentions exist (state spec has recovery states); full policy not isolated |
| 20 | State machine | **DEFINED** | state_transition_spec.json: states+transitions+invariant (21,997 bytes) |

Summary: DEFINED 11 · PARTIAL 6 · UNKNOWN 3 (restart-entry, emergency-close,
session rules) · HYPOTHESIS 0 (hypotheses live inside PARTIAL items, listed
explicitly by the spec — never promoted).

## Required sources to close the gaps (no inventing)

- restart-entry: SNIPER replay/restart datasets
- emergency-close mechanism: SNIPER forensics on emergency events
- session rules: SNIPER observation of trading-session boundaries
- PARTIAL triggers (basket close / partial close / grid anchor): SNIPER
  hypothesis-isolation experiments (uncertainty_matrix.md already tracks
  which experiments distinguish them)

## EA-side enforcement status (consumer view)

OUR-EA (ea/v1.0.0) implements the EA Build Contract 1.0.0 against this
spec (109/109 tests, LIVE_LOCKED); UNKNOWN/PARTIAL items are enforced
fail-closed per uncertainty_policy — the EA never guesses an UNKNOWN rule.
1144 Trading OS consumes signals only through the authority chain; it adds
no strategy interpretation.
