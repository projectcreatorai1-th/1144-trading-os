# RELEASE.md — 1144 Trading OS

## Release gate (all must pass)

1. `python -m architecture.validator` → 0 violations.
2. `python -m pytest` → full suite green (baseline at `os/v1.0.0`:
   2154 passed / 1 documented deployment-conditional skip / 0 failed).
3. Working tree clean; no secrets; no runtime state staged.
4. Ecosystem compatibility check green (`.github/workflows/
   cross-repo-compat.yml` against pinned tags; see `COMPATIBILITY_MATRIX.md`).

## Release unit

- Annotated tag `os/vX.Y.Z` on the verified commit (`os/v1.0.0` → `c648a4c`).
- Release evidence: phase reports under `docs/`, readiness matrices at repo
  root.

## Rollback

Two independent layers:

1. **Code/config**: git revert to the previous `os/vX.Y.Z` tag, plus the
   in-system ConfigGovernor rollback (Phase 12/15 — measured, tested
   ROLLED_BACK transitions) for configuration state.
2. **State**: Phase 12 backup/restore with hashed manifests, verified
   restore, measured RPO/RTO; recovery is fail-closed.

Ecosystem-coordinated rollback (never a single component without
compatibility checks) is being documented as `ROLLBACK.md` in ecosystem
Phase S; until then, use the compatibility matrix to select a compatible
tag set before rolling back anything.

## After release

- Push tag + main (normal push only; no force).
- Update `COMPATIBILITY_MATRIX.md` with the new OS version and gateway
  contract line.
