"""Simulation execution adapter (adapters.simulation).

A REAL production adapter for the SIMULATION environment: it produces
explicit SYNTHETIC execution evidence (never called Demo, never used outside
SIMULATION - SECTION 42). Fill prices come from the request itself so
executions are deterministic and auditable."""
from __future__ import annotations

import itertools
from typing import Any, Mapping

from adapters.simulation.sequencer import SimulationSequencer
from core.ems.adapter import (
    AdapterCapability,
    AdapterResponse,
    ConnectionState,
    ExecutionAdapter,
    PositionSemantics,
)

ADAPTER_VERSION = "1.0.0"


class SimulationExecutionAdapter(ExecutionAdapter):
    """Deterministic synthetic execution. The sequencer decides outcomes
    (fill now / partial fills / reject) from the request itself - injected
    per deployment, test scopes use controlled scripts."""

    def __init__(self, sequencer: SimulationSequencer) -> None:
        self._sequencer = sequencer
        self._connected = False
        self._sequence = itertools.count(1)

    def adapter_id(self) -> str:
        return "simulation-executor"

    def environment(self) -> str:
        return "SIMULATION"

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def health(self) -> ConnectionState:
        return ConnectionState.CONNECTED if self._connected else ConnectionState.DISCONNECTED

    def capabilities(self) -> AdapterCapability:
        return AdapterCapability(
            adapter_id=self.adapter_id(), adapter_version=ADAPTER_VERSION,
            environment=self.environment(),
            supported_symbols=("*",), order_types=("MARKET", "LIMIT"),
            time_in_force=("GTC", "IOC", "FOK", "DAY"),
            min_quantity="0.01", max_quantity="1000", quantity_step="0.01",
            price_precision=2, position_semantics=PositionSemantics.NETTING,
            partial_fill_support=True, cancel_support=True, replace_support=True,
            provenance={"source": "simulation-declaration", "observed": False},
        )

    def submit_order(self, canonical_request: Mapping[str, Any]) -> AdapterResponse:
        if not self._connected:
            return self._error_response(canonical_request, "CONNECTION_ERROR", "not connected")
        outcome = self._sequencer.next_outcome(canonical_request)
        reference = f"SIM-{next(self._sequence):08d}"
        raw = {"request": dict(canonical_request), "outcome": dict(outcome["raw"])}
        return AdapterResponse(
            ok=outcome["ok"],
            broker_order_id=outcome.get("broker_order_id") or reference,
            normalized_error=outcome.get("normalized_error"),
            raw=raw,
            raw_reference=reference,
            broker_timestamp=outcome.get("broker_timestamp"),
        )

    def cancel_order(self, broker_order_id: str) -> AdapterResponse:
        return AdapterResponse(
            ok=True, broker_order_id=broker_order_id, normalized_error=None,
            raw={"action": "cancel", "broker_order_id": broker_order_id},
            raw_reference=f"SIM-CXL-{broker_order_id}",
        )

    def replace_order(self, broker_order_id: str,
                      replacement: Mapping[str, Any]) -> AdapterResponse:
        reference = f"SIM-RPL-{broker_order_id}"
        return AdapterResponse(
            ok=True, broker_order_id=reference, normalized_error=None,
            raw={"action": "replace", "broker_order_id": broker_order_id,
                 "replacement": dict(replacement)},
            raw_reference=reference,
        )

    def poll_order(self, broker_order_id: str) -> AdapterResponse:
        return AdapterResponse(
            ok=True, broker_order_id=broker_order_id, normalized_error=None,
            raw={"action": "poll", "broker_order_id": broker_order_id,
                 "state": "FILLED"},
            raw_reference=f"SIM-POLL-{broker_order_id}",
        )

    @staticmethod
    def _error_response(request: Mapping[str, Any], error: str, detail: str) -> AdapterResponse:
        return AdapterResponse(
            ok=False, broker_order_id=None, normalized_error=error,
            raw={"request": dict(request), "detail": detail},
            raw_reference=f"SIM-ERR-{error}",
        )
