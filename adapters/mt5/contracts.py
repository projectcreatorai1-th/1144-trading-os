"""MT5 adapter interface contract (owned by adapters.mt5).

Phase 0 defines the interface only - NO live connection (SECTION 34).
Implementations in later phases must declare their environment explicitly;
environment mismatches fail closed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from adapters.broker.contracts import BrokerAdapter


class MT5Adapter(BrokerAdapter, ABC):
    """MT5-specific broker boundary. Implementation arrives in a later phase."""

    @abstractmethod
    def terminal_info(self) -> Mapping[str, Any]:  # pragma: no cover - interface definition
        ...
