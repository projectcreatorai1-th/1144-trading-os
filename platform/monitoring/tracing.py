"""End-to-end transaction tracing + time governance (Phase 14).

TraceAssembler stitches the EXISTING evidence stores (audit repository +
event store + lineage records) into one ordered trace per correlation_id.
Missing hops are REPORTED as gaps - never assumed away (UNKNOWN != SAFE).

ClockQuality records the measured provider-clock offset evidence from the
Phase 10 fix (server-local epoch) so every trace can state timestamp
confidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol

from architecture.contracts.errors import ContractError
from architecture.contracts.time import ensure_utc, utc_now

CONTRACT_VERSION = "1.0.0"


class _SupportsIteration(Protocol):
    def iter_by_correlation_id(self, correlation_id: str): ...


#: ordered hop vocabulary for gap analysis (Phase 14 trace model)
TRACE_HOPS = ("market_tick", "decision", "risk_check", "order",
              "execution", "position", "ledger", "reconciliation", "audit")


@dataclass(frozen=True)
class TraceEntry:
    hop: str
    entity_type: str
    entity_id: str
    at: datetime
    source: str
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    correlation_id: str
    entries: tuple[TraceEntry, ...]
    gaps: tuple[str, ...]      # hops with NO evidence - reported, not assumed

    def as_dict(self) -> dict:
        return {"correlation_id": self.correlation_id,
                "gaps": list(self.gaps),
                "entries": [{"hop": e.hop, "entity_type": e.entity_type,
                             "entity_id": e.entity_id,
                             "at": e.at.isoformat(), "source": e.source}
                            for e in self.entries]}


class TraceAssembler:
    """Read-only assembly over existing stores; no second source of truth."""

    def __init__(self, audit: _SupportsIteration,
                 event_reader: _SupportsIteration | None = None,
                 lineage_reader: _SupportsIteration | None = None):
        self._audit = audit
        self._events = event_reader
        self._lineage = lineage_reader

    def assemble(self, correlation_id: str,
                 *, expect_hops: Iterable[str] = TRACE_HOPS) -> Trace:
        if not correlation_id:
            raise ContractError("correlation_id required",
                                location="trace.assemble", rule_id="TRC-001")
        entries: list[TraceEntry] = []
        for record in self._audit.iter_by_correlation_id(correlation_id):
            entries.append(TraceEntry(
                hop=_hop_for(record.entity_type),
                entity_type=record.entity_type,
                entity_id=record.entity_id,
                at=ensure_utc(record.event_time),
                source=record.source,
                detail={"action": record.action}))
        for reader, kind in ((self._events, "event"),
                             (self._lineage, "lineage")):
            if reader is None:
                continue
            for record in reader.iter_by_correlation_id(correlation_id):
                entries.append(TraceEntry(
                    hop=_hop_for(getattr(record, "entity_type",
                                         getattr(record, "kind", kind))),
                    entity_type=str(getattr(record, "entity_type", kind)),
                    entity_id=str(getattr(record, "event_id",
                                          getattr(record, "lineage_id", ""))),
                    at=ensure_utc(getattr(
                        record, "event_time",
                        getattr(record, "recorded_at", utc_now()))),
                    source=kind,
                    detail={}))
        entries.sort(key=lambda e: e.at)
        present = {e.hop for e in entries if e.hop in set(expect_hops)}
        gaps = tuple(h for h in expect_hops if h not in present)
        return Trace(correlation_id=correlation_id,
                     entries=tuple(entries), gaps=gaps)


_HOP_HINTS = {
    "market_tick": "market_tick", "tick": "market_tick",
    "decision": "decision", "intent": "decision",
    "risk_decision": "risk_check", "risk": "risk_check",
    "order": "order", "intent_record": "order",
    "execution": "execution", "fill": "execution",
    "position": "position",
    "ledger_entry": "ledger", "ledger": "ledger",
    "reconciliation": "reconciliation",
    "audit": "audit",
}


def _hop_for(entity_type: str) -> str:
    return _HOP_HINTS.get(str(entity_type).lower(), str(entity_type))


# --------------------------------------------------------------------- #
# Time governance (Phase 14.3): clock quality evidence
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class ClockQuality:
    """Measured provider clock evidence. The Phase 10 fix measures the MT5
    server offset per tick; this record carries that measurement with an
    explicit confidence so consumers can state timestamp provenance."""

    source_id: str
    measured_offset_seconds: int
    method: str                 # e.g. "per-tick snap-to-half-hour"
    measured_at: datetime
    residual_tolerance_seconds: float = 300.0

    def validate(self) -> None:
        if not self.source_id:
            raise ContractError("source_id required",
                                location="clock.source_id", rule_id="CLK-001")
        if abs(self.measured_offset_seconds) > 14 * 3600:
            raise ContractError(
                "measured offset beyond timezone bounds",
                location="clock.offset", rule_id="CLK-001")
        ensure_utc(self.measured_at, location="clock.measured_at")

    def confidence(self) -> str:
        """Statement of trust carried with timestamps from this source."""
        if abs(self.measured_offset_seconds) <= 14 * 3600:
            return f"corrected: server epoch shifted by " \
                   f"{self.measured_offset_seconds}s ({self.method})"
        return "untrusted"
