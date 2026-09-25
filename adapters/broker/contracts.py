"""Broker adapter interface contract (owned by adapters.broker).

Phase 0 defines the interface only. Adapters execute; they never make risk
decisions. Every adapter declares its environment; cross-environment use
fails closed (SECTION 34).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class BrokerAdapter(ABC):
    """Generic broker boundary implemented by MT5/broker adapters later."""

    @abstractmethod
    def declared_environment(self) -> str:  # pragma: no cover - interface definition
        """The explicit environment this adapter is bound to (e.g. DEMO/LIVE)."""
        ...

    @abstractmethod
    def submit_order(self, order: Any) -> Mapping[str, Any]:  # pragma: no cover
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> Mapping[str, Any]:  # pragma: no cover
        ...
