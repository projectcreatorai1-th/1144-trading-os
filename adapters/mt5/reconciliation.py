"""MT5 reconciliation loop (owned by adapters.mt5, Phase 10).

Compares Core internal state (projected positions, account equity)
against MT5 terminal observations through the EXISTING Phase 2
ReconciliationEngine (report-only). Mismatch produces
RECONCILIATION_MISMATCH_DETECTED evidence - never an automatic mutation
of Core state to hide the difference.

LIVE is structurally refused at construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.provenance import Provenance
from architecture.contracts.time import ensure_utc, utc_now

from core.reconciliation.contracts import (
    ExternalObservation,
    ReconciliationScope,
    compute_observation_hash,
)
from core.reconciliation.engine import ReconciliationEngine

CONTRACT_VERSION = "1.0.0"


class MT5ReconciliationError(ContractError):
    rule_id = "MT5X-RECON"


@dataclass(frozen=True)
class BrokerSnapshot:
    """One observation of broker-side truth (from the transport port)."""
    account_login: str
    balance: str
    equity: str
    positions: Mapping[str, str]  # symbol -> net quantity (signed string)
    observed_at: datetime
    environment: str

    def validate(self) -> None:
        for name in ("account_login", "balance", "equity"):
            if not isinstance(getattr(self, name), str) or \
                    not getattr(self, name):
                raise MT5ReconciliationError(
                    f"snapshot.{name} must be a non-empty string",
                    location=f"snapshot.{name}")
        ensure_utc(self.observed_at, location="snapshot.observed_at")
        for symbol, quantity in self.positions.items():
            if not isinstance(symbol, str) or not symbol:
                raise MT5ReconciliationError(
                    "snapshot.positions keys must be non-empty symbols",
                    location="snapshot.positions")


def _observation(entity_type: str, entity_id: str,
                 payload: Mapping[str, Any], observed_at: datetime,
                 environment: str) -> ExternalObservation:
    # received_at follows the observation clock: a broker snapshot
    # observed at T is received no earlier than T (TIME-001)
    observation = ExternalObservation(
        observation_id=new_identifier("observation_id"),
        source="MT5", entity_type=entity_type, entity_id=entity_id,
        observed_at=observed_at,
        received_at=max(utc_now(), observed_at),
        environment=environment, payload=dict(payload),
        payload_hash=compute_observation_hash(payload),
        schema_version="1.0.0",
        provenance=Provenance(source="MT5", source_id=entity_id,
                              ingestion_time=utc_now(),
                              event_time=observed_at),
        correlation_id=new_identifier("correlation_id"))
    observation.validate()
    return observation


class MT5ReconciliationService:
    """Drives the Phase 2 engine with broker snapshots. DEMO-grade."""

    def __init__(self, *, engine: ReconciliationEngine,
                 environment: str,
                 on_event: Callable[[Mapping[str, Any]], None] | None = None,
                 ) -> None:
        if environment not in ("DEMO", "SIMULATION"):
            raise MT5ReconciliationError(
                f"reconciliation refused for environment {environment!r} "
                "(LIVE is structurally refused)",
                location="mt5_reconciliation.init",
                rule_id="MT5-LIVE-REFUSED")
        self._engine = engine
        self._environment = environment
        self._on_event = on_event
        self._mismatches = 0
        self._runs = 0

    def reconcile_positions(self, *, snapshot: BrokerSnapshot,
                            internal_positions: Mapping[str, str],
                            at: datetime | None = None) -> Mapping[str, Any]:
        snapshot.validate()
        moment = ensure_utc(at) if at else utc_now()
        observations = [
            _observation("position", symbol, {"value": quantity},
                         snapshot.observed_at, self._environment)
            for symbol, quantity in snapshot.positions.items()]
        # entities present internally but absent broker-side are still
        # compared: the engine reports missing_external for them
        result = self._engine.reconcile_observations(
            scope=ReconciliationScope.POSITION,
            environment=self._environment,
            internal_values=dict(internal_positions),
            observations=observations,
            comparison_time=moment,
            correlation_id=new_identifier("correlation_id"))
        self._runs += 1
        summary = self._summarize(result)
        self._maybe_emit_mismatch("positions", summary)
        return summary

    def reconcile_account(self, *, snapshot: BrokerSnapshot,
                          internal_equity: str,
                          at: datetime | None = None) -> Mapping[str, Any]:
        snapshot.validate()
        moment = ensure_utc(at) if at else utc_now()
        observation = _observation(
            "account_balance", snapshot.account_login,
            {"value": snapshot.equity}, snapshot.observed_at,
            self._environment)
        result = self._engine.reconcile_observations(
            scope=ReconciliationScope.BALANCE,
            environment=self._environment,
            internal_values={snapshot.account_login: internal_equity},
            observations=[observation],
            comparison_time=moment,
            correlation_id=new_identifier("correlation_id"))
        self._runs += 1
        summary = self._summarize(result)
        self._maybe_emit_mismatch("account", summary)
        return summary

    @staticmethod
    def _summarize(result) -> Mapping[str, Any]:
        return {
            "status": result.status.value,
            "matched": list(result.matched_items),
            "mismatched": [d.to_dict() for d in result.mismatched_items],
            "missing_internal": list(result.missing_internal),
            "missing_external": list(result.missing_external),
            "unknown": list(result.unknown_items),
            "reconciliation_id": result.reconciliation_id,
        }

    def _maybe_emit_mismatch(self, scope: str,
                             summary: Mapping[str, Any]) -> None:
        if summary["status"] != "MATCH":
            self._mismatches += 1
            if self._on_event is not None:
                self._on_event({
                    "event_type": "RECONCILIATION_MISMATCH_DETECTED",
                    "scope": scope, "environment": self._environment,
                    "status": summary["status"],
                    "at": utc_now().isoformat()})

    @property
    def metrics(self) -> Mapping[str, int]:
        return {"runs": self._runs, "mismatches": self._mismatches}
