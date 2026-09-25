# PROVENANCE.md — 1144 Trading OS

## Ecosystem chain (full audit: `THREE_REPO_PROVENANCE_AUDIT.md`)

```
Frozen Evidence Model (SNIPER, SHA256 124F0898…)
  → Strategy Specification 1.0.0 (SNIPER tag spec/1.0.0 → 175a259,
    canonical SHA256 4648DFBB…)
  → EA Build Contract 1.0.0 (6EFBDA5A…)
  → OUR-EA runtime 1.0.0 (tag ea/v1.0.0 → 5cb5a06;
    sealed manifest runs/demo_1.0.0/deployment_manifest.json)
  → Gateway Contract 1.0.0 (client: SNIPER + OUR-EA; server: this repo)
  → 1144 Trading OS (tag os/v1.0.0 → c648a4c; system 1.1.0,
    internal contracts 1.10.0)
  → adapters.mt5 → MT5 terminal → Broker
```

## What this repository enforces at runtime

- Gateway handshake: client `contract_version` must match the `1.0` line
  (`platform/gateway/contracts.py`); mismatch → CONTRACT_ERROR, fail-closed.
- Client identity/sessions are surfaced through the gateway session layer;
  the OS does NOT read SNIPER artifacts — the EA is the spec consumer.
- Internal contracts (1.10.0) are validated by the architecture validator
  against every module dependency (234+ rules).

## Verifying the ecosystem set (read-only)

Given the three tags `spec/1.0.0`, `ea/v1.0.0`, `os/v1.0.0`:

1. OUR-EA `verify()` against the SNIPER checkout at `spec/1.0.0` → PASS
   proves L1/L2 (spec → EA, build contract → EA).
2. Gateway contract constants in all three repos → all `1.0.0` proves L4.
3. EA manifest hashes == SNIPER `spec_version.json` pins proves L5/L6.

This is automated by `.github/workflows/cross-repo-compat.yml` (pinned to
tags, never `latest`).
