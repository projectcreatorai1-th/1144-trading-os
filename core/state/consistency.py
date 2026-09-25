"""State + ledger consistency checks (owned by core.state).

CURRENT STATE vs REBUILT STATE, and LEDGER BALANCE vs RECONSTRUCTED BALANCE.
Results: CONSISTENT / INCONSISTENT / UNKNOWN - UNKNOWN is never consistent
(SECTION 32)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable

from core.events.contracts import Event, EventType
from core.events.store import EventStore
from core.ledger.balance import BalanceReconstructor
from core.ledger.store import LedgerStore
from core.state.contracts import StateCategory, StateRecord
from core.state.projector import EventStateProjector
from core.state.rebuilder import StateRebuilder
from core.state.stores import StateStore

CONTRACT_VERSION = "1.0.0"


class ConsistencyStatus(Enum):
    CONSISTENT = "CONSISTENT"
    INCONSISTENT = "INCONSISTENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ConsistencyReport:
    check: str
    status: ConsistencyStatus
    details: dict[str, str]

    @property
    def consistent(self) -> bool:
        return self.status is ConsistencyStatus.CONSISTENT


class StateConsistencyChecker:
    def __init__(self, states: StateStore, events: EventStore, projector: EventStateProjector):
        self._states = states
        self._rebuilder = StateRebuilder(events, projector)

    def check_entity(self, entity_type: StateCategory, entity_id: str) -> ConsistencyReport:
        current = self._states.get_current_state(entity_type.value, entity_id)
        rebuilt = self._rebuilder.rebuild(entity_type=entity_type, entity_id=entity_id).state
        if current is None and rebuilt is None:
            return ConsistencyReport(
                check=f"{entity_type.value}:{entity_id}",
                status=ConsistencyStatus.CONSISTENT,
                details={"note": "no state on either side"},
            )
        if current is None or rebuilt is None:
            return ConsistencyReport(
                check=f"{entity_type.value}:{entity_id}",
                status=ConsistencyStatus.INCONSISTENT,
                details={
                    "current": "present" if current else "absent",
                    "rebuilt": "present" if rebuilt else "absent",
                },
            )
        same = (
            current.status == rebuilt.status
            and current.state_version == rebuilt.state_version
            and _canonical_payload(current) == _canonical_payload(rebuilt)
        )
        return ConsistencyReport(
            check=f"{entity_type.value}:{entity_id}",
            status=ConsistencyStatus.CONSISTENT if same else ConsistencyStatus.INCONSISTENT,
            details={
                "current": f"v{current.state_version}:{current.status}",
                "rebuilt": f"v{rebuilt.state_version}:{rebuilt.status}",
            },
        )


class LedgerConsistencyChecker:
    """LEDGER BALANCE (store) vs RECONSTRUCTED BALANCE (LEDGER_POSTED events)."""

    def __init__(self, ledger: LedgerStore, events: EventStore,
                 balances: BalanceReconstructor):
        self._ledger = ledger
        self._events = events
        self._balances = balances

    def check_account(self, account_id: str, currency: str) -> ConsistencyReport:
        report = self._balances.reconstruct(account_id, currency=currency)
        reconstructed: dict[str, Decimal] = {}
        saw_events = False
        for event in self._events.query_by_type(EventType.LEDGER_POSTED.value):
            saw_events = True
            payload = event.payload
            if payload.get("account_id") != account_id or payload.get("currency") != currency:
                continue
            reconstructed["total"] = reconstructed.get("total", Decimal("0")) + Decimal(
                str(payload.get("amount", "0"))
            )
        if not saw_events and report.entry_count == 0:
            return ConsistencyReport(
                check=f"ledger:{account_id}:{currency}",
                status=ConsistencyStatus.UNKNOWN,
                details={"reason": "no ledger entries and no posting events"},
            )
        if not saw_events:
            return ConsistencyReport(
                check=f"ledger:{account_id}:{currency}",
                status=ConsistencyStatus.UNKNOWN,
                details={"reason": "no LEDGER_POSTED events to reconstruct from"},
            )
        event_total = reconstructed.get("total", Decimal("0")) + report.opening_balance
        same = event_total == report.closing_balance
        return ConsistencyReport(
            check=f"ledger:{account_id}:{currency}",
            status=ConsistencyStatus.CONSISTENT if same else ConsistencyStatus.INCONSISTENT,
            details={
                "store_balance": str(report.closing_balance),
                "event_reconstructed": str(event_total),
            },
        )


def _canonical_payload(state: StateRecord) -> str:
    import json

    return json.dumps(state.payload, sort_keys=True, separators=(",", ":"), default=str)
