"""Phase 2 state contract + engine tests (SECTIONS 5-8)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    EnvironmentMismatchError,
    StateTransitionError,
)
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType
from core.state.contracts import (
    CONTRACT_VERSION,
    MACHINE_BY_CATEGORY,
    StateCategory,
    StateRecord,
    StateSnapshot,
    StateTransitionRecord,
    compute_snapshot_hash,
)
from core.state.engine import StateEngine
from tests.factories import make_event
from tests.phase1_factories import at


def make_state(**overrides):
    defaults = dict(
        state_id=new_identifier("state_id"),
        entity_type=StateCategory.SYSTEM_STATE,
        entity_id="1144-os",
        state_version=1,
        status="READY",
        payload={"mode": "paper"},
        effective_time=at(11, 0),
        observed_time=at(11, 0),
        processed_time=at(11, 0, 1),
        source_event_id=new_identifier("event_id"),
        correlation_id=new_identifier("correlation_id"),
        environment="SIMULATION",
        schema_version=CONTRACT_VERSION,
    )
    defaults.update(overrides)
    return StateRecord(**defaults)


class TestStateRecordContract:
    def test_valid_state(self):
        make_state().validate()

    def test_machine_categories_mapped(self):
        assert MACHINE_BY_CATEGORY[StateCategory.SYSTEM_STATE] == "system_state"
        assert MACHINE_BY_CATEGORY[StateCategory.MARKET_STATE] == "market_state"
        assert MACHINE_BY_CATEGORY[StateCategory.ORDER_STATE] == "order_state"
        assert MACHINE_BY_CATEGORY[StateCategory.POSITION_STATE] == "position_status"
        for category in (StateCategory.DATA_STATE, StateCategory.ACCOUNT_STATE,
                         StateCategory.LEDGER_STATE, StateCategory.RECONCILIATION_STATE):
            assert category not in MACHINE_BY_CATEGORY

    def test_time_ordering_enforced(self):
        with pytest.raises(ContractValidationError):
            make_state(observed_time=at(10, 59), effective_time=at(11, 0)).validate()

    def test_processed_before_observed_rejected(self):
        with pytest.raises(ContractValidationError):
            make_state(processed_time=at(10, 59)).validate()

    def test_naive_time_rejected(self):
        with pytest.raises(ContractValidationError):
            make_state(effective_time=datetime(2026, 9, 23, 11, 0)).validate()

    def test_invalid_event_reference_rejected(self):
        with pytest.raises(ContractValidationError):
            make_state(source_event_id="event-1").validate()

    def test_version_must_be_positive(self):
        with pytest.raises(ContractValidationError):
            make_state(state_version=0).validate()

    def test_state_immutable(self):
        state = make_state()
        with pytest.raises(FrozenInstanceError):
            state.status = "RUNNING"

    def test_roundtrip_from_storage(self):
        state = make_state()
        rebuilt = StateRecord.from_storage(state.to_dict())
        assert rebuilt == state


class TestTransitionContract:
    def _transition(self, **overrides):
        defaults = dict(
            transition_id=new_identifier("transition_id"),
            entity_type=StateCategory.SYSTEM_STATE,
            entity_id="1144-os",
            new_state="READY",
            previous_version=0,
            new_version=1,
            event_id=new_identifier("event_id"),
            reason="startup complete",
            actor="platform.runtime",
            timestamp=at(11, 0),
            environment="SIMULATION",
            correlation_id=new_identifier("correlation_id"),
            previous_state=None,
        )
        defaults.update(overrides)
        return StateTransitionRecord(**defaults)

    def test_genesis_transition(self):
        self._transition().validate()

    def test_non_genesis_requires_previous_state(self):
        with pytest.raises(ContractValidationError):
            self._transition(previous_version=1, new_version=2, previous_state=None).validate()

    def test_genesis_must_not_have_previous_state(self):
        with pytest.raises(ContractValidationError):
            self._transition(previous_state="STARTING").validate()

    def test_version_must_increment(self):
        with pytest.raises(ContractValidationError):
            self._transition(previous_version=1, new_version=3, previous_state="STARTING").validate()


class TestSnapshotContract:
    def _snapshot(self, **overrides):
        payload = {"status": "READY", "data": {"mode": "paper"}}
        defaults = dict(
            snapshot_id=new_identifier("snapshot_id"),
            entity_type=StateCategory.SYSTEM_STATE,
            entity_id="1144-os",
            state_version=1,
            event_sequence=5,
            event_id=new_identifier("event_id"),
            created_at=at(11, 5),
            state_payload=payload,
            schema_version=CONTRACT_VERSION,
            environment="SIMULATION",
            hash=compute_snapshot_hash(
                entity_type="SYSTEM_STATE", entity_id="1144-os", state_version=1,
                event_sequence=5, state_payload=payload,
            ),
        )
        defaults.update(overrides)
        return StateSnapshot(**defaults)

    def test_valid_snapshot(self):
        self._snapshot().validate()

    def test_corrupted_hash_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            self._snapshot(hash="0" * 64).validate()
        assert excinfo.value.rule_id == "STATE-003"

    def test_snapshot_immutable(self):
        snapshot = self._snapshot()
        with pytest.raises(FrozenInstanceError):
            snapshot.state_version = 2

    def test_hash_depends_on_payload(self):
        import dataclasses

        first = self._snapshot()
        changed_payload = {"status": "RUNNING"}
        changed = dataclasses.replace(
            first,
            state_payload=changed_payload,
            hash=compute_snapshot_hash(
                entity_type="SYSTEM_STATE", entity_id="1144-os", state_version=1,
                event_sequence=5, state_payload=changed_payload,
            ),
        )
        assert first.hash != changed.hash
        changed.validate()


class TestStateEngine:
    ENGINE = StateEngine()

    def _event(self, **overrides):
        overrides.setdefault("environment", "SIMULATION")
        overrides.setdefault("event_type", EventType.SYSTEM_STATE_CHANGED)
        return make_event(**overrides)

    def test_genesis_transition_through_machine(self):
        event = self._event()
        application = self.ENGINE.apply_event(
            entity_type=StateCategory.SYSTEM_STATE,
            entity_id="1144-os",
            current=None,
            new_status="READY",
            event=event,
            payload={"mode": "paper"},
            reason="startup complete",
            actor="platform.runtime",
            effective_time=at(11, 0),
            observed_time=at(11, 0),
            processed_time=at(11, 0, 1),
        )
        assert application.state.status == "READY"
        assert application.state.state_version == 1
        assert application.transition.previous_state is None
        assert application.machine_transition is not None
        assert application.state.source_event_id == event.event_id

    def test_machine_transition_chain(self):
        first = self.ENGINE.apply_event(
            entity_type=StateCategory.SYSTEM_STATE, entity_id="os", current=None,
            new_status="READY", event=self._event(), payload={"mode": "paper"},
            reason="r", actor="a",
            effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
        )
        second = self.ENGINE.apply_event(
            entity_type=StateCategory.SYSTEM_STATE, entity_id="os", current=first.state,
            new_status="RUNNING", event=self._event(causation_id=first.state.source_event_id),
            payload={"load": 1}, reason="all subsystems up", actor="platform.runtime",
            effective_time=at(11, 1), observed_time=at(11, 1), processed_time=at(11, 1, 0),
        )
        assert second.state.state_version == 2
        assert second.transition.previous_state == "READY"
        assert second.state.payload.get("load") == 1
        assert second.state.payload.get("mode") == "paper"  # payload merge preserves history

    def test_invalid_machine_transition_fails_closed(self):
        with pytest.raises(StateTransitionError):
            self.ENGINE.apply_event(
                entity_type=StateCategory.SYSTEM_STATE, entity_id="os", current=None,
                new_status="PARTY", event=self._event(), payload={}, reason="r", actor="a",
                effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
            )

    def test_market_state_unknown_is_valid(self):
        application = self.ENGINE.apply_event(
            entity_type=StateCategory.MARKET_STATE, entity_id="XAUUSD", current=None,
            new_status="UNKNOWN", event=self._event(), payload={}, reason="feed gap",
            actor="core.data.projector",
            effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
        )
        assert application.state.status == "UNKNOWN"

    def test_non_machine_category_free_status(self):
        application = self.ENGINE.apply_event(
            entity_type=StateCategory.DATA_STATE, entity_id="feed-xauusd", current=None,
            new_status="DEGRADED", event=self._event(), payload={"rule": "DQ-007"},
            reason="stale data", actor="core.data.projector",
            effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
        )
        assert application.machine_transition is None
        assert application.state.status == "DEGRADED"

    def test_environment_mismatch_fails_closed(self):
        first = self.ENGINE.apply_event(
            entity_type=StateCategory.SYSTEM_STATE, entity_id="os", current=None,
            new_status="READY", event=self._event(), payload={}, reason="r", actor="a",
            effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
        )
        with pytest.raises(EnvironmentMismatchError):
            self.ENGINE.apply_event(
                entity_type=StateCategory.SYSTEM_STATE, entity_id="os", current=first.state,
                new_status="RUNNING", event=self._event(environment="LIVE"), payload={},
                reason="r", actor="a",
                effective_time=at(11, 1), observed_time=at(11, 1), processed_time=at(11, 1, 0),
            )

    def test_empty_status_rejected(self):
        with pytest.raises(ContractValidationError):
            self.ENGINE.apply_event(
                entity_type=StateCategory.DATA_STATE, entity_id="f", current=None,
                new_status="", event=self._event(), payload={}, reason="r", actor="a",
                effective_time=at(11, 0), observed_time=at(11, 0), processed_time=at(11, 0, 1),
            )
