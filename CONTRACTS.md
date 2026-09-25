# CONTRACTS.md — 1144 Trading OS

## Contracts owned here

| Contract | Version | Where | Notes |
| --- | --- | --- | --- |
| Internal architecture contracts | 1.10.0 | `architecture/architecture.yaml`, `architecture/manifest.yaml` | identifiers 1.10.0, schemas, state machines, tolerances; validated by the architecture validator |
| Gateway SERVER contract | 1.0.0 | `platform/gateway/contracts.py` (`CONTRACT_VERSION`) | field-for-field implementation of the shared SNIPER Gateway contract v1.0.0; serves OUR_EA / ANALYZER / MT5 / TRADING_OS client sessions |
| Gateway event/signal schemas | 1.0.0 | `platform/gateway/contracts.py` (`EVENT_SCHEMA_VERSION`, `SIGNAL_SCHEMA_VERSION`) | wire envelope identical fields/meaning to contract v1.0.0 |

## Contracts consumed (read-only compat surfaces)

| Contract | Version | Owner |
| --- | --- | --- |
| Strategy Specification / EA Build Contract / Frozen Evidence Model | 1.0.0 / 1.0.0 / V1.68 | SNIPER (this repo consumes only the gateway handshake identity of its clients; it does not read spec artifacts) |

## Compatibility enforcement

- Gateway sessions must present `contract_version` with prefix `1.0`
  (`platform/gateway/contracts.py`); mismatches are rejected (CONTRACT_ERROR).
- Internal contract/schema versions are governed by
  `architecture/manifest.yaml` and the validator; breaking changes bump
  MAJOR and require the full test gate.

## Change protocol

1. Update `architecture/*.yaml` (SSOT) first.
2. Run `python -m architecture.validator` → must be 0 violations.
3. Run the full suite (`python -m pytest`) → must be green.
4. Gateway contract changes additionally require cross-repo agreement with
   SNIPER and OUR-EA clients (see `COMPATIBILITY_MATRIX.md`) — the gateway
   is the ecosystem's compatibility surface.
