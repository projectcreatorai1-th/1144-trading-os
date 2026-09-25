# THREE_REPO_PROVENANCE_AUDIT.md — 1144 Trading Ecosystem

Generated 2026-09-25 · Cross-repository provenance audit (Phase C of the
ecosystem master plan). All values verified live from the three working
trees at audit time. No artifact was regenerated or modified for this audit.

## Dependency map (as verified)

```
Frozen Evidence Model                [OWNER: SNIPER]
  data/evidence_model/V1.68-EVIDENCE-MODEL-v1.0.json (+ .sha256.txt sidecar)
        ↓ deterministic generation (SHA256 canonical JSON)
Strategy Specification 1.0.0        [OWNER: SNIPER]
  STRATEGY_SPEC/ (11 files; spec_version.json self-pins every artifact hash)
        ↓ hash compatibility gate
EA Build Contract 1.0.0             [OWNER: SNIPER]
  STRATEGY_SPEC/ea_build_contract.json (re-pins spec hash)
        ↓ consumed via sanctioned path only (verify() three-way check)
OUR-EA runtime 1.0.0                [OWNER: OUR-EA]
  ../SNIPER-CashFlow-Analyzer | env SNIPER_ANALYZER_ROOT
  runs/demo_1.0.0/deployment_manifest.json (sealed pins)
        ↓ Gateway Contract 1.0.0 (EA client ↔ OS server)
1144 Trading OS system 1.1.0        [OWNER: 1144-Trading-OS]
  platform.gateway (server, contract 1.0.0) + core engines (contracts 1.10.0)
        ↓ adapters.mt5 (DEMO-gated, LIVE structurally refused)
MT5 terminal → Broker
```

## Repository state at audit time

| Repository | Branch | HEAD commit | Tags | Remote | Working tree |
| --- | --- | --- | --- | --- | --- |
| SNIPER-CashFlow-Analyzer | main | `175a25952ec80cf66bd9050dc5daa58eb81f3e27` (`chore(spec): pin strategy provenance to b7c87b4`) | `spec/1.0.0` → 175a259 | origin = github.com/projectcreatorai1-th/sniper-web (origin/main at b7c87b4 — NOT pushed) | clean |
| OUR-EA | main | `5cb5a060e78e1fa175ac3f4b1b87ab0b6ee07f38` (`chore(repo): establish OUR-EA version-control baseline`) | (none yet — ea/v1.0.0 planned Phase F) | none | clean |
| 1144-Trading-OS | main | `c648a4cafaf06e83e770db1c27779a60c09a8cab` (`chore(repo): establish 1144 Trading OS version-control baseline`) | (none yet — os/v1.0.0 planned Phase F) | none | clean |

## Artifact ownership (single source of truth)

| Artifact | Sole owner / producer | Consumers | Verified by |
| --- | --- | --- | --- |
| Frozen Evidence Model (hash `124F08984284E880F268C37E6A4583A8653B5EB1B80EB5C45BBB64F58AC3D4AE`) | SNIPER (single writer: freeze script; tracked in git with sidecar) | spec generation; OUR-EA (hard pin in `our_ea/spec/repository.py`) | OUR-EA verify() + sidecar + spec_version pin |
| Strategy Specification 1.0.0 (canonical hash `4648DFBB8AD54B8274EB5F81C87EA47F5AEE18DF7F90DAB1A90099B6F0E7C89E`) | SNIPER (`STRATEGY_SPEC/`) — generated from commit `b7c87b4`, committed at `175a259`, tagged `spec/1.0.0` | OUR-EA (sanctioned path only) | three-way: canonical(spec) == spec_version pin == build-contract pin |
| EA Build Contract 1.0.0 (hash `6EFBDA5A9273977428B6CFDDB0117795E6A304C3B831787D3531687A18AEBFF3`) | SNIPER | OUR-EA (compatibility gate) | spec_version pin + EA deployment manifest pin |
| Replay dataset v1 (hash `994955EA1889409A3B71C15C799B2802E0D9CF5E42DB277D420FA0835B3C0705`) | SNIPER (`data/our_ea/replay_dataset_v1.json` + sidecar, git-tracked) | OUR-EA (replay validation) | hard pin in EA source |
| Gateway Contract 1.0.0 | shared compat surface — SNIPER client (`core/gateway/contracts.py`) + OUR-EA client (`our_ea/gateway/contracts.py`) + Trading OS server (`platform/gateway/contracts.py`, field-for-field) | all three | EA handshake version check + OS session contract_version prefix gate |
| EA deployment manifest (spec `4648DF…`, evidence `124F08…`, gateway `1.0.0`, deployment hash `CF3889FDBE660D04…`, live_locked=true, mode PAPER) | OUR-EA (`runs/demo_1.0.0/`, git-tracked) | audit/provenance | committed blob == disk, verified |

## Role verification

- **SNIPER is the producer**: only `SNIPER-CashFlow-Analyzer/` contains
  `STRATEGY_SPEC/`, the evidence model, and the deterministic spec generator.
  No other repository contains a Strategy Spec source (verified by tree
  search) — NO_DUPLICATED_SPEC holds.
- **OUR-EA is the consumer/enforcer**: reads artifacts only through
  `our_ea/spec/repository.py` (sanctioned path), verifies three-way hash
  consistency at load, hard-pins evidence model + replay dataset hashes in
  source, refuses to start on any mismatch (fail-closed, INV-009). Never
  imports analyzer runtime code (boundary test in its suite).
- **1144-Trading-OS is the gateway/runtime infrastructure**: owns the ONE
  gateway server (contract 1.0.0), core engines (strategy/risk/execution
  authorities), MT5 adapter (DEMO-gated; LIVE structurally refused).
  It does not duplicate spec authority.

## Verification evidence (this audit)

- OUR-EA `load_repository().verify()` → `verified: True`, spec `4648DFBB…`,
  evidence `124F0898…`, build contract 1.0.0 compatible (17 states / 62
  transitions consistent).
- Trading OS architecture validator → PASS (0 failures, 0 warnings).
- Trading OS full suite → 2154 passed / 1 skipped (deployment-conditional,
  documented) / 0 failed.
- OUR-EA suite → 109/109 passed (pre- and post-baseline-commit).
- Spec three-way consistency verified against **committed blobs** of
  `175a259` (not merely the working tree).

## Known gaps (carried to Phase D)

1. SNIPER local `main` (175a259) and tag `spec/1.0.0` are **not pushed**;
   origin/main is one commit behind (b7c87b4). Provenance exists in local
   history only until Phase F push.
2. OUR-EA baseline commit and Trading OS baseline commit have **no release
   tags yet** (planned: `ea/v1.0.0`, `os/v1.0.0` in Phase F).
3. EA deployment manifest predates the EA git baseline and therefore carries
   no EA `git_sha` field (remediation belongs to future release manifests,
   Phase Q — the sealed run artifact must not be edited retroactively).
4. SNIPER `core/gateway/contracts.py` header comment cites Trading OS
   contracts "1.4.0" (actual current: 1.10.0) — stale comment only, no
   behavioral effect; left untouched per no-unnecessary-change rule.

## Acceptance

```
SINGLE_SOURCE_OF_TRUTH = PASS
PROVENANCE_CHAIN       = PASS
NO_DUPLICATED_SPEC     = PASS
CONTRACT_BOUNDARIES    = PASS
```
