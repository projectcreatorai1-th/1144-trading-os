"""Phase 14 - trace assembly, gap detection, time governance."""
from __future__ import annotations

from datetime import timedelta

import pytest

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.monitoring.tracing import (
    TRACE_HOPS,
    ClockQuality,
    TraceAssembler,
)


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return next((r for r in self.records if r.audit_id == audit_id), None)

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


def _record(correlation_id, entity_type, entity_id, minutes_ago):
    return AuditRecord(
        audit_id=new_identifier("audit_id"), actor_type=ActorType.SYSTEM,
        actor_id="t", action=f"{entity_type.upper()}_STEP",
        entity_type=entity_type, entity_id=entity_id,
        event_time=utc_now() - timedelta(minutes=minutes_ago),
        before=None, after={}, reason="trace test", source="test",
        environment="SIMULATION", correlation_id=correlation_id)


class TestTraceAssembler:
    def test_orders_entries_and_reports_gaps(self):
        correlation_id = new_identifier("event_id")
        audit = FakeAudit()
        audit.append(_record(correlation_id, "market_tick", "raw_1", 30))
        audit.append(_record(correlation_id, "decision", "dec_1", 29))
        audit.append(_record(correlation_id, "risk_decision", "rsk_1", 28))
        audit.append(_record(correlation_id, "order", "ord_1", 27))
        trace = TraceAssembler(audit).assemble(correlation_id)
        assert [e.hop for e in trace.entries] == [
            "market_tick", "decision", "risk_check", "order"]
        # hops without evidence are REPORTED (never assumed)
        assert set(trace.gaps) == {"execution", "position", "ledger",
                                   "reconciliation", "audit"}
        assert trace.entries[0].at <= trace.entries[-1].at

    def test_full_chain_has_no_gaps(self):
        correlation_id = new_identifier("event_id")
        audit = FakeAudit()
        for hop_entity in ("market_tick", "decision", "risk_decision",
                           "order", "execution", "position", "ledger_entry",
                           "reconciliation", "audit"):
            audit.append(_record(correlation_id, hop_entity, "x", 1))
        trace = TraceAssembler(audit).assemble(correlation_id)
        assert trace.gaps == ()
        assert len(trace.entries) == 9

    def test_empty_trace_reports_all_gaps(self):
        trace = TraceAssembler(FakeAudit()).assemble(
            new_identifier("event_id"))
        assert set(trace.gaps) == set(TRACE_HOPS)

    def test_correlation_id_required(self):
        with pytest.raises(ContractError):
            TraceAssembler(FakeAudit()).assemble("")


class TestClockQuality:
    def test_phase10_offset_evidence_carries_confidence(self):
        clock = ClockQuality(source_id="mt5-feed",
                             measured_offset_seconds=10800,
                             method="per-tick snap-to-half-hour",
                             measured_at=utc_now())
        clock.validate()
        assert "10800s" in clock.confidence()

    def test_out_of_bounds_rejected(self):
        with pytest.raises(ContractError):
            ClockQuality(source_id="mt5-feed",
                         measured_offset_seconds=20 * 3600,
                         method="x", measured_at=utc_now()).validate()
