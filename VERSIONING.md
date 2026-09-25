# VERSIONING.md — 1144 Trading OS

| Layer | Form | Current |
| --- | --- | --- |
| Code version | git commit SHA + tags | `1a37046` (baseline `c648a4c`, tag `os/v1.0.0` → `c648a4c`) |
| Product (system) | semver | 1.1.0 (`architecture/manifest.yaml` → `system_version`) |
| Architecture/contract/schema | semver | 1.10.0 (same manifest; identifiers 1.10.0) |
| Gateway contract | semver | 1.0.0 (shared with SNIPER/OUR-EA clients) |
| Artifact hash | SHA256 | ecosystem artifact hashes owned by SNIPER (see `COMPATIBILITY_MATRIX.md`) |

## Tag scheme

- `os/vX.Y.Z` — annotated tag on a verified release commit.
  `os/v1.0.0` → `c648a4c` (baseline: validator 0 violations; 2154 passed /
  1 documented deployment-conditional skip / 0 failed).

## Rules

- `system_version` / contract versions move ONLY via `architecture/`
  registry updates that pass the validator — never as loose constants.
- The ecosystem-wide component/version table is `COMPATIBILITY_MATRIX.md`
  in this repository (this repo is the integration hub).
- No second version source may contradict the manifest or the matrix.
