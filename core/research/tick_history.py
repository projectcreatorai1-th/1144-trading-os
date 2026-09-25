"""Tick history -> research plane (owned by core.research, Phase 10).

D8.4: real ticks flow into the EXISTING research contracts
(Observation + ResearchDataset) - there is no second research pipeline.
Storage is bounded (config-driven), immutable per dataset version, and
preserves event time, ingestion time, provider and symbol.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Tuple

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from core.research.contracts import Observation, ResearchDataset

CONTRACT_VERSION = "1.0.0"
SYNTHETIC = False  # this recorder stores REAL provider ticks


class TickHistoryError(ContractError):
    rule_id = "FDX-HISTORY"


@dataclass(frozen=True)
class RecordedTick:
    symbol: str
    event_time: datetime
    ingestion_time: datetime
    bid: str
    ask: str
    last: str
    volume: str
    sequence: int

    def observation(self) -> Observation:
        return Observation(
            symbol=self.symbol, event_time=self.event_time,
            available_time=self.ingestion_time,
            payload={"bid": self.bid, "ask": self.ask, "last": self.last,
                     "volume": self.volume,
                     "sequence": self.sequence, "provider": "MT5",
                     "ingestion_time": self.ingestion_time.isoformat()},
            data_type="MARKET_DATA")


class TickHistoryRecorder:
    """Bounded in-memory buffer of validated ticks; flush() freezes an
    immutable ResearchDataset version (research plane contract)."""

    def __init__(self, config) -> None:
        # config: ConnectivityConfig (imported lazily by callers to
        # keep core.research independent of adapters)
        self._config = config
        self._ticks: deque[RecordedTick] = deque()
        self._seen: set[Tuple[str, int]] = set()
        self._datasets: list[ResearchDataset] = []
        self._duplicates = 0

    # ---------------- recording ---------------- #
    def record_tick(self, tick) -> None:
        """Called by the feed adapter for every ACCEPTED tick. Duplicate
        detection by (symbol, sequence); corruption detection happens at
        dataset construction (content hash)."""
        recorded = RecordedTick(
            symbol=tick.symbol,
            event_time=ensure_utc(tick.event_time),
            ingestion_time=ensure_utc(tick.ingestion_time),
            bid=str(tick.bid), ask=str(tick.ask), last=str(tick.last),
            volume=str(tick.volume),
            sequence=tick.sequence if tick.sequence is not None else
            int(tick.event_time.timestamp() * 1000))
        key = (recorded.symbol, recorded.sequence)
        if key in self._seen:
            self._duplicates += 1
            return  # DO NOT PROCESS TWICE
        self._seen.add(key)
        self._ticks.append(recorded)
        self._enforce_bounds()

    def _enforce_bounds(self) -> None:
        max_ticks = self._config.tick_history.max_ticks
        cutoff = utc_now() - timedelta(
            days=self._config.tick_history.max_age_days)
        while len(self._ticks) > max_ticks or (
                self._ticks and self._ticks[0].event_time < cutoff):
            evicted = self._ticks.popleft()
            self._seen.discard((evicted.symbol, evicted.sequence))

    # ---------------- research plane ---------------- #
    def flush(self, *, environment: str = "RESEARCH",
              created_at: datetime | None = None) -> ResearchDataset:
        """Freeze the current buffer into an immutable, content-hashed
        ResearchDataset version (PIT semantics: available_time =
        ingestion_time)."""
        if not self._ticks:
            raise TickHistoryError(
                "tick history is empty - refusing to fabricate a dataset",
                location="tick_history.flush", rule_id="FDX-HISTORY")
        observations = tuple(t.observation()
                             for t in sorted(self._ticks,
                                             key=lambda t: t.event_time))
        symbols = tuple(sorted({o.symbol for o in observations}))
        dataset = ResearchDataset(
            dataset_id=new_identifier("dataset_id"),
            dataset_version="1.0.0",
            symbols=symbols,
            time_range={"start": observations[0].event_time.isoformat(),
                        "end": observations[-1].event_time.isoformat()},
            timeframe="TICK", timezone="UTC", data_source="MT5",
            source_versions={"provider": "MT5-terminal"},
            quality_summary={"pipeline": "phase-1-ingestion",
                             "duplicates_skipped": self._duplicates},
            lineage={"origin": "mt5 tick feed (Phase 10)",
                     "synthetic": SYNTHETIC},
            content_hash="",
            created_at=ensure_utc(created_at) if created_at else utc_now(),
            environment=environment,
            observations=observations)
        object.__setattr__(dataset, "content_hash",
                           dataset.compute_content_hash())
        dataset.validate()  # corruption detection: hash + PIT rules
        self._datasets.append(dataset)
        self._ticks.clear()
        return dataset

    # ---------------- evidence ---------------- #
    @property
    def buffered(self) -> int:
        return len(self._ticks)

    @property
    def datasets(self) -> Tuple[ResearchDataset, ...]:
        return tuple(self._datasets)

    @property
    def duplicates_skipped(self) -> int:
        return self._duplicates

    def metrics(self) -> Mapping[str, int]:
        return {"buffered": len(self._ticks),
                "datasets_flushed": len(self._datasets),
                "duplicates_skipped": self._duplicates}
