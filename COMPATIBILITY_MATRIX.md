# COMPATIBILITY_MATRIX.md — 1144 Trading Ecosystem

Canonical component/version table (this repository is the integration hub).
Last updated: 2026-09-25 (ecosystem Phase H).

| Component | Version | Required | Git anchor |
| --- | ------: | -------: | ---------- |
| Frozen Evidence Model | V1.68-EVIDENCE-MODEL-v1.0 (SHA256 `124F0898…`) | — | SNIPER repo (tracked artifact + sidecar) |
| Strategy Spec | 1.0.0 (SHA256 `4648DFBB…`) | Evidence Model `124F0898…` | SNIPER tag `spec/1.0.0` → `175a259` |
| EA Build Contract | 1.0.0 (SHA256 `6EFBDA5A…`) | Spec 1.0.0 (`4648DFBB…`) | SNIPER tag `spec/1.0.0` |
| Replay dataset | v1 (SHA256 `994955EA…`) | — | SNIPER repo (tracked + sidecar) |
| OUR-EA | 1.0.0 | EA Contract 1.0.0 · Spec MAJOR 1 | tag `ea/v1.0.0` → `5cb5a06` |
| Gateway Contract | 1.0.0 | field-compatible on all three sides | constants in SNIPER / OUR-EA / Trading OS |
| 1144 Trading OS | system 1.1.0 · contracts 1.10.0 | Gateway 1.0.0 server line | tag `os/v1.0.0` → `c648a4c` |

## Verified compatible set (2026-09-25)

```text
SNIPER      spec/1.0.0  (175a259, pushed to origin)
OUR-EA      ea/v1.0.0   (5cb5a06, local)
Trading OS  os/v1.0.0   (c648a4c, local)
Gateway     1.0.0       (all three repos)
```

Evidence: `THREE_REPO_PROVENANCE_AUDIT.md` (acceptance all PASS);
automated re-verification: `.github/workflows/cross-repo-compat.yml`
(pinned to the tags above — never `latest`).

## Version model (summary; details in each repo's VERSIONING.md)

- **Git SHA / tag** — code identity (`spec/x.y.z`, `ea/vX.Y.Z`, `os/vX.Y.Z`)
- **Product semver** — SNIPER evidence lineage V1.68 · EA runtime 1.0.0 ·
  OS system 1.1.0
- **Contract semver** — Spec 1.0.0 · Build Contract 1.0.0 · Gateway 1.0.0 ·
  OS internal 1.10.0 (MAJOR = breaking)
- **Artifact SHA256** — canonical-JSON hashes pinned in `spec_version.json`,
  sidecars, EA source pins, deployment manifest

Rules: no layer substitutes for another; consumers pin by hash (artifacts)
or version line (contracts), never by branch.
