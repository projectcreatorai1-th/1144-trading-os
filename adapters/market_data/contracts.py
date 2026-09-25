"""Market data adapter interface contract (owned by adapters.market_data).

Phase 0 defines the interface only; ingestion arrives in Phase 1+.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Iterator


class MarketDataAdapter(ABC):
    @abstractmethod
    def stream_observations(self, symbol: str) -> Iterator[Any]:  # pragma: no cover - interface definition
        ...
