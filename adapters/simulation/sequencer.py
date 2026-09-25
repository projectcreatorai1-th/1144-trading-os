"""Simulation sequencer (adapters.simulation).

Decides synthetic execution outcomes deterministically from the request.
Production deployments inject a live-market-driven sequencer; the default
fills at the requested/last-known price immediately."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


class SimulationSequencer:
    def next_outcome(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        price = request.get("price") or "1.00"
        now = datetime.now(__import__("datetime").timezone.utc)
        return {
            "ok": True,
            "normalized_error": None,
            "broker_order_id": None,
            "broker_timestamp": now,
            "raw": {
                "synthetic": True,
                "fill_price": str(price),
                "filled_quantity": str(request.get("quantity", "0")),
                "environment": "SIMULATION",
            },
        }


__all__ = ["SimulationSequencer"]
