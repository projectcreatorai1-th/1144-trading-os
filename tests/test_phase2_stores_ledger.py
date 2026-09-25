"""Phase 2 store + ledger posting + balance tests (SECTIONS 16-20)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractValidationError, StorageError
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.ledger.balance import BalanceReconstructor
from core.ledger.contracts import LedgerEntry, LedgerType
from core.ledger.posting import LedgerDraft, LedgerPostingService, compute_idempotency_key
from core.state.contracts import StateCategory, StateRecord, StateSnapshot, compute_snapshot_hash
from core.state.engine import StateEngine
from platform.database.sqlite_stores import StorageSet
from tests.factories import make_event
from tests.phase1_factories import at


@pytest.fixture()
def storage(tmp_path):
    storage = StorageSet(tmp_path / "phase2.db")
    yield storage
    storage.close()


def make_application(storage, status="READY", version=1, previous=None):
    engine = StateEngine()
    event = make_event(environment="SIMULATION")
    return engine.apply_event(
        entity_type=StateCategory.SYSTEM_STATE, entity_id="1144-os", current=previous,
        new_status=status, event=event, payload={"tick": version}, reason="test",
        actor="tester", effective_time=at(11, version), observed_time=at(11, version),
        processed_time=at(11, version, 1),
    )


class TestStateStore:
    def test_save_and_current(self, storage):
        application = make_application(storage)
        storage.states.save_state(application.state, application.transition)
        current = storage.states.get_current_state("SYSTEM_STATE", "1144-os")
        assert current.state_id == application.state.state_id
        assert current.state_version == 1

    def test_history_and_at_time(self, storage):
        first = make_application(storage, status="READY", version=1)
        storage.states.save_state(first.state, first.transition)
        second = make_application(storage, status="RUNNING", version=2, previous=first.state)
        storage.states.save_state(second.state, second.transition)
        history = list(storage.states.get_state_history("SYSTEM_STATE", "1144-os"))
        assert [s.state_version for s in history] == [1, 2]
        at_first = storage.states.get_state_at_time("SYSTEM_STATE", "1144-os", at(11, 1))
        assert at_first.status == "READY"
        at_second = storage.states.get_state_at_time("SYSTEM_STATE", "1144-os", at(11, 2))
        assert at_second.status == "RUNNING"

    def test_event_idempotency(self, storage):
        application = make_application(storage)
        storage.states.save_state(application.state, application.transition)
        assert storage.states.has_applied_event("SYSTEM_STATE", "1144-os", application.state.source_event_id)
        with pytest.raises(StorageError):
            storage.states.save_state(application.state, application.transition)

    def test_state_records_immutable(self, storage):
        application = make_application(storage)
        with pytest.raises(FrozenInstanceError):
            application.state.status = "RUNNING"


class TestSnapshotStore:
    def test_append_and_latest(self, storage):
        application = make_application(storage)
        payload = {"status": application.state.status, "data": dict(application.state.payload)}
        snapshot = StateSnapshot(
            snapshot_id=new_identifier("snapshot_id"),
            entity_type=StateCategory.SYSTEM_STATE,
            entity_id="1144-os",
            state_version=1,
            event_sequence=1,
            event_id=application.state.source_event_id,
            created_at=at(11, 5),
            state_payload=payload,
            schema_version="1.0.0",
            environment="SIMULATION",
            hash=compute_snapshot_hash(
                entity_type="SYSTEM_STATE", entity_id="1144-os", state_version=1,
                event_sequence=1, state_payload=payload,
            ),
        )
        storage.snapshots.append(snapshot)
        latest = storage.snapshots.latest_for("SYSTEM_STATE", "1144-os")
        assert latest.snapshot_id == snapshot.snapshot_id
        loaded = storage.snapshots.get_by_id(snapshot.snapshot_id)
        assert loaded.hash == snapshot.hash

    def test_corrupted_snapshot_rejected(self, storage):
        import dataclasses

        application = make_application(storage)
        payload = {"status": "READY"}
        snapshot = StateSnapshot(
            snapshot_id=new_identifier("snapshot_id"),
            entity_type=StateCategory.SYSTEM_STATE,
            entity_id="1144-os", state_version=1, event_sequence=1,
            event_id=application.state.source_event_id, created_at=at(11, 5),
            state_payload=payload, schema_version="1.0.0", environment="SIMULATION",
            hash=compute_snapshot_hash(
                entity_type="SYSTEM_STATE", entity_id="1144-os", state_version=1,
                event_sequence=1, state_payload=payload,
            ),
        )
        tampered = dataclasses.replace(snapshot, state_payload={"status": "RUNNING"})
        with pytest.raises(ContractValidationError):
            storage.snapshots.append(tampered)


class TestLedgerPosting:
    def _service(self, storage):
        return LedgerPostingService(storage.ledger, storage.audit)

    def _event(self, **overrides):
        overrides.setdefault("environment", "SIMULATION")
        return make_event(**overrides)

    def test_post_creates_hashed_entry_with_audit(self, storage):
        service = self._service(storage)
        event = self._event()
        result = service.post(
            LedgerDraft(
                entry_type=LedgerType.CASH, account_id="ACC-1", amount="1000.00",
                currency="USD", reason="initial funding",
            ),
            event=event, entry_time=at(12, 0),
        )
        assert result.created
        entry = result.entry
        assert entry.entry_hash is not None
        assert entry.previous_entry_hash is None
        assert storage.ledger.count() == 1
        audits = list(storage.audit.iter_by_correlation_id(event.correlation_id))
        assert any(a.action == "LEDGER_POSTED" for a in audits)

    def test_hash_chain_builds_per_account(self, storage):
        service = self._service(storage)
        first = service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-1", amount="100",
                        currency="USD", reason="a"),
            event=self._event(), entry_time=at(12, 0),
        ).entry
        second = service.post(
            LedgerDraft(entry_type=LedgerType.FEE, account_id="ACC-1", amount="-2.50",
                        currency="USD", reason="b"),
            event=self._event(), entry_time=at(12, 1),
        ).entry
        other = service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-2", amount="50",
                        currency="USD", reason="c"),
            event=self._event(), entry_time=at(12, 2),
        ).entry
        assert second.previous_entry_hash == first.entry_hash
        assert other.previous_entry_hash is None  # separate account chain
        assert storage.ledger.verify_account_chain("ACC-1") == []

    def test_idempotent_repost_returns_same_entry(self, storage):
        service = self._service(storage)
        draft = LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-1",
                            amount="100", currency="USD", reason="a")
        event = self._event()
        first = service.post(draft, event=event, entry_time=at(12, 0))
        assert first.created
        again = service.post(draft, event=event, entry_time=at(12, 5))
        assert not again.created
        assert again.entry.ledger_entry_id == first.entry.ledger_entry_id
        assert storage.ledger.count() == 1  # duplicate event cannot duplicate canonical ledger

    def test_idempotency_key_deterministic(self):
        key1 = compute_idempotency_key(
            source_event_id="evt_" + "1" * 32, entry_type="CASH",
            account_id="A", currency="USD", amount=Decimal("100"),
        )
        key2 = compute_idempotency_key(
            source_event_id="evt_" + "1" * 32, entry_type="CASH",
            account_id="A", currency="USD", amount=Decimal("100"),
        )
        key3 = compute_idempotency_key(
            source_event_id="evt_" + "2" * 32, entry_type="CASH",
            account_id="A", currency="USD", amount=Decimal("100"),
        )
        key4 = compute_idempotency_key(
            source_event_id="evt_" + "1" * 32, entry_type="FEE",
            account_id="A", currency="USD", amount=Decimal("100"),
        )
        assert key1 == key2  # same event + same semantics = same key
        assert key1 != key3  # different event -> different key
        assert key1 != key4  # different entry type -> different key

    def test_float_amount_rejected(self, storage):
        service = self._service(storage)
        with pytest.raises(ContractValidationError) as excinfo:
            service.post(
                LedgerDraft(entry_type=LedgerType.CASH, account_id="A", amount=10.5,
                            currency="USD", reason="x"),
                event=self._event(), entry_time=at(12, 0),
            )
        assert excinfo.value.rule_id == "LEDGER-004"

    def test_unknown_currency_rejected(self, storage):
        service = self._service(storage)
        with pytest.raises(ContractValidationError):
            service.post(
                LedgerDraft(entry_type=LedgerType.CASH, account_id="A", amount="10",
                            currency="ZZZ", reason="x"),
                event=self._event(), entry_time=at(12, 0),
            )

    def test_adjustment_requires_reference(self, storage):
        service = self._service(storage)
        with pytest.raises(ContractValidationError) as excinfo:
            service.post(
                LedgerDraft(entry_type=LedgerType.ADJUSTMENT, account_id="A", amount="10",
                            currency="USD", reason="correction"),
                event=self._event(), entry_time=at(12, 0),
            )
        assert excinfo.value.rule_id == "LEDGER-002"

    def test_reversal_keeps_original(self, storage):
        service = self._service(storage)
        event = self._event()
        original = service.post(
            LedgerDraft(entry_type=LedgerType.FEE, account_id="ACC-1", amount="-10",
                        currency="USD", reason="fee"),
            event=event, entry_time=at(12, 0),
        ).entry
        reversal = service.reverse(original, event=self._event(), entry_time=at(12, 1),
                                    reason="wrong fee")
        assert reversal.amount == Decimal("10")
        assert reversal.reverses_entry_id == original.ledger_entry_id
        # original untouched and still present
        reloaded = storage.ledger.get_by_id(original.ledger_entry_id)
        assert reloaded.amount == Decimal("-10")
        assert reloaded.reverses_entry_id is None
        assert storage.ledger.verify_account_chain("ACC-1") == []

    def test_broken_chain_rejected_at_store(self, storage):
        service = self._service(storage)
        service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="A", amount="1",
                        currency="USD", reason="a"),
            event=self._event(), entry_time=at(12, 0),
        )
        forged = LedgerEntry(
            ledger_entry_id=new_identifier("ledger_entry_id"),
            entry_type=LedgerType.CASH, account_id="A", amount=Decimal("2"),
            currency="USD", environment="SIMULATION", entry_time=at(12, 1),
            correlation_id=new_identifier("correlation_id"),
            source_event_id=new_identifier("event_id"),
            previous_entry_hash=None,  # forged: skips chain
            idempotency_key="forged-key-1",
        )
        object.__setattr__(forged, "entry_hash", forged.compute_hash())
        with pytest.raises(StorageError) as excinfo:
            storage.ledger.append(forged)
        assert "hash chain" in excinfo.value.message.lower()

    def test_ledger_mutation_blocked(self, storage):
        service = self._service(storage)
        entry = service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="A", amount="1",
                        currency="USD", reason="a"),
            event=self._event(), entry_time=at(12, 0),
        ).entry
        with pytest.raises(FrozenInstanceError):
            entry.amount = Decimal("999")


class TestBalanceReconstruction:
    def test_reconstruct_with_buckets(self, storage):
        service = LedgerPostingService(storage.ledger, storage.audit)
        for draft in (
            LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-1", amount="1000.00",
                        currency="USD", reason="funding"),
            LedgerDraft(entry_type=LedgerType.FEE, account_id="ACC-1", amount="-2.50",
                        currency="USD", reason="fee"),
            LedgerDraft(entry_type=LedgerType.COMMISSION, account_id="ACC-1", amount="-0.50",
                        currency="USD", reason="commission"),
            LedgerDraft(entry_type=LedgerType.SWAP, account_id="ACC-1", amount="1.00",
                        currency="USD", reason="swap"),
        ):
            service.post(draft, event=make_event(environment="SIMULATION"), entry_time=at(12, 0))
        report = BalanceReconstructor(storage.ledger).reconstruct("ACC-1", currency="USD")
        assert report.closing_balance == Decimal("998.00")
        assert report.by_type["FEE"] == Decimal("-2.50")
        assert report.entry_count == 4

    def test_multi_currency_separated(self, storage):
        service = LedgerPostingService(storage.ledger, storage.audit)
        service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-1", amount="100",
                        currency="USD", reason="a"),
            event=make_event(environment="SIMULATION"), entry_time=at(12, 0),
        )
        service.post(
            LedgerDraft(entry_type=LedgerType.CASH, account_id="ACC-1", amount="500",
                        currency="EUR", reason="b"),
            event=make_event(environment="SIMULATION"), entry_time=at(12, 1),
        )
        reconstructor = BalanceReconstructor(storage.ledger)
        assert reconstructor.reconstruct("ACC-1", currency="USD").closing_balance == Decimal("100")
        assert reconstructor.reconstruct("ACC-1", currency="EUR").closing_balance == Decimal("500")

    def test_opening_balance(self, storage):
        report = BalanceReconstructor(storage.ledger).reconstruct(
            "ACC-9", currency="USD", opening_balance="250.00"
        )
        assert report.closing_balance == Decimal("250.00")
