"""Phase 2 invariant tests (SECTION 50) + E2E (SECTION 51).

E2E: EVENT -> STATE -> SNAPSHOT -> LEDGER -> RECONCILIATION -> AUDIT,
then RESTART -> LOAD -> REBUILD -> VERIFY with identical results, plus the
full trace ledger -> state -> event -> raw data."""
from __future__ import annotations

from decimal import Decimal

import pytest

from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.ledger.balance import BalanceReconstructor
from core.ledger.contracts import LedgerType
from core.ledger.posting import LedgerDraft, LedgerPostingService
from core.reconciliation.contracts import (
    ReconciliationScope,
    ReconciliationStatus,
    compute_observation_hash,
)
from core.reconciliation.engine import ReconciliationEngine
from core.reconciliation.contracts import ExternalObservation
from architecture.contracts.provenance import Provenance
from core.state.consistency import ConsistencyStatus, LedgerConsistencyChecker, StateConsistencyChecker
from core.state.contracts import StateCategory
from core.state.processor import EventStateLedgerProcessor
from core.state.projector import EventStateProjector
from core.state.snapshot import SnapshotService
from platform.database.sqlite_stores import StorageSet
from tests.factories import make_event
from tests.phase1_factories import at


def build_world(storage):
    posting = LedgerPostingService(storage.ledger, storage.audit)
    processor = EventStateLedgerProcessor(
        states=storage.states, events=storage.events, posting=posting,
        audit=storage.audit,
    )
    processor.register_ledger_handler("ECONOMIC_EVENT", lambda event: LedgerDraft(
        entry_type=LedgerType.CASH,
        account_id=event.payload["account_id"],
        amount=event.payload["deposit_amount"],
        currency=event.payload.get("currency", "USD"),
        reason="cash movement from event",
    ) if "deposit_amount" in event.payload else None)
    return posting, processor


def deposit_event(amount: str, account="ACC-E2E", when=None, correlation=None):
    when = when or at(11, 0)
    return make_event(
        event_type=EventType.ECONOMIC_EVENT,
        payload={"deposit_amount": amount, "account_id": account, "currency": "USD"},
        event_time=when, received_time=when,
        correlation_id=correlation or new_identifier("correlation_id"),
    )


def system_event(state, when=None, correlation=None):
    when = when or at(11, 0)
    return make_event(
        event_type=EventType.SYSTEM_STATE_CHANGED,
        payload={"state": state, "system": "1144-os"},
        event_time=when, received_time=when,
        correlation_id=correlation or new_identifier("correlation_id"),
    )


def observation(entity_id, value, observed_at=None):
    observed = observed_at or at(11, 30)
    payload = {"value": value}
    obs = ExternalObservation(
        observation_id=new_identifier("observation_id"), source="bank-statement",
        entity_type="account_balance", entity_id=entity_id,
        observed_at=observed, received_at=observed, environment="SIMULATION",
        payload=payload, payload_hash=compute_observation_hash(payload),
        schema_version="1.0.0",
        provenance=Provenance(source="bank-statement", source_id=entity_id,
                              ingestion_time=observed, event_time=observed),
        correlation_id=new_identifier("correlation_id"),
    )
    obs.validate()
    return obs


class TestInvariants:
    @pytest.fixture()
    def world(self, tmp_path):
        storage = StorageSet(tmp_path / "inv.db")
        _, processor = build_world(storage)
        for event in (system_event("READY"), deposit_event("1000"), deposit_event("-250")):
            storage.events.append(event)
            processor.process(event)
        for event in list(storage.events.query_by_type("LEDGER_POSTED")):
            processor.process(event)
        yield storage
        storage.close()

    def test_1_historical_state_immutable(self, world):
        from dataclasses import FrozenInstanceError

        state = world.states.get_current_state("SYSTEM_STATE", "1144-os")
        with pytest.raises(FrozenInstanceError):
            state.status = "HACKED"

    def test_2_historical_ledger_immutable(self, world):
        from dataclasses import FrozenInstanceError

        for entry in world.ledger.iter_by_account("ACC-E2E"):
            with pytest.raises(FrozenInstanceError):
                entry.amount = Decimal("999")
            break

    def test_3_historical_reconciliation_immutable(self, tmp_path):
        from dataclasses import FrozenInstanceError

        storage = StorageSet(tmp_path / "inv3.db")
        engine = ReconciliationEngine(storage.reconciliations, storage.audit)
        result = engine.compare_values_map(
            scope=ReconciliationScope.BALANCE, environment="SIMULATION",
            internal_values={"A": "1"}, external_values={"A": "2"},
            source="test", comparison_time=at(12, 0),
        )
        with pytest.raises(FrozenInstanceError):
            result.status = ReconciliationStatus.MATCH
        storage.close()

    def test_4_same_events_same_state(self, world):
        projector = EventStateProjector()
        checker = StateConsistencyChecker(world.states, world.events, projector)
        assert checker.check_entity(StateCategory.SYSTEM_STATE, "1144-os").status \
            is ConsistencyStatus.CONSISTENT
        assert checker.check_entity(StateCategory.ACCOUNT_STATE, "ACC-E2E").status \
            is ConsistencyStatus.CONSISTENT

    def test_5_same_ledger_same_balance(self, world):
        checker = LedgerConsistencyChecker(
            world.ledger, world.events, BalanceReconstructor(world.ledger)
        )
        assert checker.check_account("ACC-E2E", "USD").status is ConsistencyStatus.CONSISTENT

    def test_6_duplicate_event_no_duplicate_ledger(self, world):
        _, processor = build_world(world)
        before = world.ledger.count()
        duplicate = deposit_event("1000")  # same amount, NEW event id -> posts once (different event)
        world.events.append(duplicate)
        processor.process(duplicate)
        assert world.ledger.count() == before + 1
        # replaying the SAME stored events creates nothing new
        processor.recover()
        assert world.ledger.count() == before + 1

    def test_7_reversal_never_deletes_original(self, world):
        posting = LedgerPostingService(world.ledger, world.audit)
        entries = list(world.ledger.iter_by_account("ACC-E2E"))
        original = entries[0]
        reversal_event = deposit_event("0", account="ACC-E2E")
        world.events.append(reversal_event)
        posting.reverse(original, event=reversal_event, entry_time=at(11, 45),
                        reason="correction of wrong booking")
        reloaded = world.ledger.get_by_id(original.ledger_entry_id)
        assert reloaded.amount == original.amount
        assert world.ledger.verify_account_chain("ACC-E2E") == []

    def test_8_state_references_valid_event(self, world):
        event_ids = {event.event_id for event in world.events.iter_all()}
        for entity_type in ("SYSTEM_STATE", "ACCOUNT_STATE"):
            for state in _iter_states(world, entity_type):
                assert state.source_event_id in event_ids

    def test_11_environment_isolation_not_bypassed(self, world):
        from architecture.contracts.errors import EnvironmentMismatchError

        # a SIMULATION reconciliation can never consume a LIVE observation
        live_observation = observation("ACC-LIVE", "1")
        object.__setattr__(live_observation, "environment", "LIVE")
        object.__setattr__(live_observation, "payload_hash",
                           compute_observation_hash(live_observation.payload))
        live_observation.validate()
        engine = ReconciliationEngine(world.reconciliations, world.audit)
        with pytest.raises(EnvironmentMismatchError):
            engine.reconcile_observations(
                scope=ReconciliationScope.BALANCE, environment="SIMULATION",
                internal_values={"ACC-LIVE": "1"},
                observations=[live_observation],
                comparison_time=at(11, 50),
            )

    def test_12_decimal_money_deterministic(self, world):
        report = BalanceReconstructor(world.ledger).reconstruct("ACC-E2E", currency="USD")
        assert report.closing_balance == Decimal("750")
        report2 = BalanceReconstructor(world.ledger).reconstruct("ACC-E2E", currency="USD")
        assert report2.closing_balance == report.closing_balance


def _iter_states(storage, entity_type):
    return list(storage.states.get_state_history(entity_type, _first_entity(storage, entity_type)))


def _first_entity(storage, entity_type):
    for state in storage.states.get_state_history(entity_type, "1144-os" if entity_type == "SYSTEM_STATE" else "ACC-E2E"):
        return state.entity_id
    return "1144-os"


class TestEndToEnd:
    def test_full_chain_and_restart_rebuild_verify(self, tmp_path):
        path = tmp_path / "e2e.db"
        storage = StorageSet(path)
        posting, processor = build_world(storage)

        # EVENT -> STATE -> LEDGER
        correlation = new_identifier("correlation_id")
        events = [
            system_event("READY", when=at(11, 0), correlation=correlation),
            deposit_event("1000", when=at(11, 1), correlation=correlation),
            deposit_event("-250", when=at(11, 2), correlation=correlation),
        ]
        for event in events:
            storage.events.append(event)
            processor.process(event)
        for event in list(storage.events.query_by_type("LEDGER_POSTED")):
            processor.process(event)

        # SNAPSHOT
        current = storage.states.get_current_state("ACCOUNT_STATE", "ACC-E2E")
        snapshots = SnapshotService(storage.snapshots, storage.audit)
        snapshot = snapshots.create(
            state=current,
            event_sequence=storage.events.get_sequence(current.source_event_id),
            last_event_id=current.source_event_id,
            created_at=at(11, 10),
        )

        # LEDGER -> balance
        report = BalanceReconstructor(storage.ledger).reconstruct("ACC-E2E", currency="USD")
        assert report.closing_balance == Decimal("750")

        # RECONCILIATION (internal balance vs external observation)
        engine = ReconciliationEngine(storage.reconciliations, storage.audit)
        result = engine.reconcile_observations(
            scope=ReconciliationScope.BALANCE, environment="SIMULATION",
            internal_values={"ACC-E2E": report.closing_balance},
            observations=[observation("ACC-E2E", "750")],
            comparison_time=at(11, 30),
        )
        assert result.status is ReconciliationStatus.MATCH

        # AUDIT trace
        audit_actions = [
            record.action
            for record in storage.audit.iter_by_correlation_id(correlation)
        ]
        assert "STATE_TRANSITION" in audit_actions
        assert "LEDGER_POSTED" in audit_actions
        assert "SNAPSHOT_CREATED" in audit_actions

        # TRACE: ledger -> state -> event -> raw lineage (raw via Phase 1 lineage)
        entry = next(storage.ledger.iter_by_account("ACC-E2E"))
        assert entry.source_event_id is not None
        source_event = storage.events.get_by_id(entry.source_event_id)
        account_state = storage.states.get_current_state("ACCOUNT_STATE", "ACC-E2E")
        assert account_state.source_event_id in {
            event.event_id for event in storage.events.iter_all()
        }
        assert source_event.correlation_id == correlation
        state_before_restart = storage.states.get_current_state("SYSTEM_STATE", "1144-os")
        storage.close()

        # RESTART -> LOAD -> REBUILD -> VERIFY
        storage2 = StorageSet(path)
        projector = EventStateProjector()
        state_checker = StateConsistencyChecker(storage2.states, storage2.events, projector)
        for entity in ((StateCategory.SYSTEM_STATE, "1144-os"),
                       (StateCategory.ACCOUNT_STATE, "ACC-E2E")):
            report_c = state_checker.check_entity(*entity)
            assert report_c.status is ConsistencyStatus.CONSISTENT, report_c.details
        ledger_checker = LedgerConsistencyChecker(
            storage2.ledger, storage2.events, BalanceReconstructor(storage2.ledger)
        )
        assert ledger_checker.check_account("ACC-E2E", "USD").status is ConsistencyStatus.CONSISTENT

        state_after = storage2.states.get_current_state("SYSTEM_STATE", "1144-os")
        assert state_after.status == state_before_restart.status
        assert state_after.state_version == state_before_restart.state_version
        loaded_snapshot = storage2.snapshots.get_by_id(snapshot.snapshot_id)
        assert loaded_snapshot.hash == snapshot.hash
        storage2.close()
