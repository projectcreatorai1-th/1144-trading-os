"""Phase 2 projector, rebuild, consistency, recovery tests (SECTIONS 9-12, 32-35)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.ledger.balance import BalanceReconstructor
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerDraft, LedgerPostingService
from core.state.consistency import (
    ConsistencyStatus,
    LedgerConsistencyChecker,
    StateConsistencyChecker,
)
from core.state.contracts import StateCategory
from core.state.processor import EventStateLedgerProcessor
from core.state.projector import EventStateProjector
from core.state.rebuilder import StateRebuilder
from core.state.snapshot import SnapshotService
from platform.database.sqlite_stores import StorageSet
from tests.factories import make_event
from tests.phase1_factories import at


@pytest.fixture()
def world(tmp_path):
    storage = StorageSet(tmp_path / "state.db")
    posting = LedgerPostingService(storage.ledger, storage.audit)
    projector = EventStateProjector()
    processor = EventStateLedgerProcessor(
        states=storage.states, events=storage.events, posting=posting,
        audit=storage.audit, projector=projector,
    )

    def cash_handler(event):
        payload = event.payload
        if "deposit_amount" not in payload:
            return None
        return LedgerDraft(
            entry_type=LedgerType.CASH,
            account_id=payload["account_id"],
            amount=payload["deposit_amount"],
            currency=payload.get("currency", "USD"),
            reason="deposit observed in event",
        )

    processor.register_ledger_handler("ECONOMIC_EVENT", cash_handler)
    yield {"storage": storage, "posting": posting, "projector": projector, "processor": processor}
    storage.close()


def system_event(state: str, correlation=None):
    return make_event(
        event_type=EventType.SYSTEM_STATE_CHANGED,
        payload={"state": state, "system": "1144-os"},
        event_time=at(11, 0), received_time=at(11, 0),
        correlation_id=correlation or new_identifier("correlation_id"),
    )


def deposit_event(amount: str, account: str = "ACC-1", correlation=None):
    return make_event(
        event_type=EventType.ECONOMIC_EVENT,
        payload={"deposit_amount": amount, "account_id": account, "currency": "USD"},
        event_time=at(11, 5), received_time=at(11, 5),
        correlation_id=correlation or new_identifier("correlation_id"),
    )


class TestProjector:
    def test_system_state_projection(self, world):
        application = world["projector"].apply(system_event("READY"), world["storage"].states)
        assert application is not None
        assert application.state.entity_type is StateCategory.SYSTEM_STATE
        assert application.state.status == "READY"

    def test_ledger_posted_projects_account_state(self, world):
        world["processor"].process(deposit_event("1000"))
        # the LEDGER_POSTED event (emitted by the processor) projects ACCOUNT_STATE
        posted_events = list(world["storage"].events.query_by_type("LEDGER_POSTED"))
        assert len(posted_events) == 1
        application = world["projector"].apply(posted_events[0], world["storage"].states)
        assert application is not None
        assert application.state.entity_type is StateCategory.ACCOUNT_STATE
        assert application.state.payload["balances"] == {"USD": "1000"}

    def test_unrelated_event_ignored(self, world):
        assert world["projector"].apply(make_event(), world["storage"].states) is None

    def test_projector_is_idempotent_per_entity_event(self, world):
        event = system_event("READY")
        first = world["projector"].apply(event, world["storage"].states)
        assert first is not None
        second = world["projector"].apply(event, world["storage"].states)
        assert second is None  # already applied


class TestRebuild:
    def _seed(self, world):
        correlation = new_identifier("correlation_id")
        events = [
            system_event("STARTING", correlation),
            system_event("READY", correlation),
            system_event("RUNNING", correlation),
        ]
        for event in events:
            world["storage"].events.append(event)
            world["processor"].process(event)
        return events

    def test_same_events_same_state(self, world):
        self._seed(world)
        rebuilder = StateRebuilder(world["storage"].events, world["projector"])
        first = rebuilder.rebuild(entity_type=StateCategory.SYSTEM_STATE, entity_id="1144-os")
        second = rebuilder.rebuild(entity_type=StateCategory.SYSTEM_STATE, entity_id="1144-os")
        assert first.state is not None and second.state is not None
        assert first.state.status == second.state.status == "RUNNING"
        assert first.state.state_version == second.state.state_version == 3
        assert first.state.payload == second.state.payload

    def test_rebuild_matches_current(self, world):
        self._seed(world)
        checker = StateConsistencyChecker(
            world["storage"].states, world["storage"].events, world["projector"]
        )
        report = checker.check_entity(StateCategory.SYSTEM_STATE, "1144-os")
        assert report.status is ConsistencyStatus.CONSISTENT

    def test_rebuild_from_snapshot(self, world):
        self._seed(world)
        current = world["storage"].states.get_current_state("SYSTEM_STATE", "1144-os")
        snapshot_service = SnapshotService(world["storage"].snapshots, world["storage"].audit)
        snapshot = snapshot_service.create(
            state=current,
            event_sequence=world["storage"].events.get_sequence(current.source_event_id),
            last_event_id=current.source_event_id,
            created_at=at(11, 10),
        )
        # one more transition after the snapshot
        extra = system_event("DEGRADED")
        world["storage"].events.append(extra)
        world["processor"].process(extra)

        rebuilder = StateRebuilder(
            world["storage"].events, world["projector"], world["storage"].snapshots
        )
        rebuilt = rebuilder.rebuild(
            entity_type=StateCategory.SYSTEM_STATE, entity_id="1144-os",
            from_snapshot=snapshot,
        )
        assert rebuilt.state.status == "DEGRADED"
        assert rebuilt.applied_events == 1  # only the post-snapshot event applied


class TestInconsistentDetection:
    def test_divergent_current_detected(self, world):
        event = system_event("READY")
        world["storage"].events.append(event)
        world["processor"].process(event)
        # corrupt: current state exists but event store emptied is impossible;
        # instead simulate divergence by removing state history write -> rebuild finds nothing
        storage2_events_only = world["storage"]
        # craft divergence: apply an extra event to state WITHOUT storing the event
        extra = system_event("RUNNING")
        application = world["projector"].project(
            extra, storage2_events_only.states.get_current_state("SYSTEM_STATE", "1144-os")
        )
        storage2_events_only.states.save_state(application.state, application.transition)
        checker = StateConsistencyChecker(
            storage2_events_only.states, storage2_events_only.events, world["projector"]
        )
        report = checker.check_entity(StateCategory.SYSTEM_STATE, "1144-os")
        assert report.status is ConsistencyStatus.INCONSISTENT


class TestLedgerConsistency:
    def test_store_balance_matches_event_reconstruction(self, world):
        world["processor"].process(deposit_event("1000"))
        world["processor"].process(deposit_event("250.50"))
        # project the emitted LEDGER_POSTED events into ACCOUNT_STATE
        for event in list(world["storage"].events.query_by_type("LEDGER_POSTED")):
            world["projector"].apply(event, world["storage"].states)
        checker = LedgerConsistencyChecker(
            world["storage"].ledger, world["storage"].events,
            BalanceReconstructor(world["storage"].ledger),
        )
        report = checker.check_account("ACC-1", "USD")
        assert report.status is ConsistencyStatus.CONSISTENT
        assert report.details["store_balance"] == "1250.50"

    def test_unknown_when_no_evidence(self, world):
        checker = LedgerConsistencyChecker(
            world["storage"].ledger, world["storage"].events,
            BalanceReconstructor(world["storage"].ledger),
        )
        report = checker.check_account("ACC-404", "USD")
        assert report.status is ConsistencyStatus.UNKNOWN


class TestCrashRecovery:
    def test_partial_write_recovers_idempotently(self, world):
        """Crash between state write and ledger write: state applied, ledger
        not posted. Recovery reprocesses; state skips, ledger posts once."""
        event = deposit_event("500")
        world["storage"].events.append(event)
        # state side only (no ledger handler wired in this simulation)
        world["projector"].apply(event, world["storage"].states)
        assert world["storage"].ledger.count() == 0

        outcomes = world["processor"].recover()
        assert any(o.ledger_posted for o in outcomes)
        assert world["storage"].ledger.count() == 1
        # recover again: still one canonical entry, no duplicates
        world["processor"].recover()
        assert world["storage"].ledger.count() == 1
        assert world["storage"].ledger.verify_account_chain("ACC-1") == []
        checker = LedgerConsistencyChecker(
            world["storage"].ledger, world["storage"].events,
            BalanceReconstructor(world["storage"].ledger),
        )
        assert checker.check_account("ACC-1", "USD").status is ConsistencyStatus.CONSISTENT

    def test_restart_reload_rebuild_verify(self, tmp_path):
        path = tmp_path / "restart.db"
        storage = StorageSet(path)
        posting = LedgerPostingService(storage.ledger, storage.audit)
        processor = EventStateLedgerProcessor(
            states=storage.states, events=storage.events, posting=posting,
            audit=storage.audit,
        )
        processor.register_ledger_handler("ECONOMIC_EVENT", lambda e: LedgerDraft(
            entry_type=LedgerType.CASH, account_id=e.payload["account_id"],
            amount=e.payload["deposit_amount"], currency="USD", reason="deposit",
        ))
        for event in (system_event("READY"), deposit_event("100"), deposit_event("-30")):
            storage.events.append(event)
            processor.process(event)
        state_before = storage.states.get_current_state("SYSTEM_STATE", "1144-os")
        balance_before = BalanceReconstructor(storage.ledger).reconstruct("ACC-1", currency="USD")
        storage.close()

        # restart
        storage2 = StorageSet(path)
        projector = EventStateProjector()
        checker = StateConsistencyChecker(storage2.states, storage2.events, projector)
        assert checker.check_entity(StateCategory.SYSTEM_STATE, "1144-os").status \
            is ConsistencyStatus.CONSISTENT
        state_after = storage2.states.get_current_state("SYSTEM_STATE", "1144-os")
        assert state_after.status == state_before.status
        assert state_after.state_version == state_before.state_version
        balance_after = BalanceReconstructor(storage2.ledger).reconstruct("ACC-1", currency="USD")
        assert balance_after.closing_balance == balance_before.closing_balance == Decimal("70")
        storage2.close()
