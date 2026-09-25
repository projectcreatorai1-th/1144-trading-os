"""Event contract tests (SECTION 9) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    IdentifierValidationError,
    TimeValidationError,
)
from architecture.contracts.identifiers import new_identifier
from core.events.contracts import EventType, build_event
from tests.factories import at, make_event

ALL_EVENT_TYPES = {
    "MARKET_DATA_RECEIVED", "NEWS_RECEIVED", "ECONOMIC_EVENT", "MARKET_STATE_CHANGED",
    "AI_ANALYSIS_COMPLETED", "STRATEGY_SIGNAL_CREATED", "POLICY_EVALUATED",
    "RISK_EVALUATED", "ORDER_CREATED", "ORDER_SUBMITTED", "ORDER_ACCEPTED",
    "ORDER_REJECTED", "ORDER_FILLED", "ORDER_PARTIALLY_FILLED", "POSITION_OPENED",
    "POSITION_UPDATED", "POSITION_CLOSED", "RECONCILIATION_COMPLETED", "LEDGER_POSTED",
    "SYSTEM_STATE_CHANGED", "RISK_STATE_CHANGED",
    # Phase 1 foundation types (event contract 1.1.0)
    "DATA_QUALITY_CHANGED", "DATA_SOURCE_CHANGED", "SYSTEM_TIME_ANOMALY",
    "EVENT_REJECTED", "EVENT_ACCEPTED", "REPLAY_STARTED", "REPLAY_COMPLETED",
    # Phase 7 intelligence types (event contract 1.2.0, advisory plane only)
    "FEATURE_CREATED", "INTELLIGENCE_DATASET_CREATED", "TRAINING_STARTED",
    "TRAINING_COMPLETED", "TRAINING_FAILED", "MODEL_REGISTERED",
    "MODEL_VALIDATED", "MODEL_SUSPENDED", "MODEL_RETIRED",
    "INFERENCE_COMPLETED", "AI_PROPOSAL_CREATED", "DRIFT_DETECTED",
    "MODEL_REPLAYED",    # Phase 10 connectivity types (event contract 1.4.0)
    "CONNECTION_STATE_CHANGED", "FEED_STALE", "FEED_RESYNC",
    "RECONCILIATION_MISMATCH_DETECTED",
    # Phase 8 security/governance types (event contract 1.3.0)
    "AUTHENTICATION_SUCCEEDED", "AUTHENTICATION_FAILED", "SESSION_CREATED",
    "SESSION_EXPIRED", "SESSION_REVOKED", "CREDENTIAL_ISSUED",
    "CREDENTIAL_ROTATED", "CREDENTIAL_REVOKED", "PERMISSION_GRANTED",
    "PERMISSION_REVOKED", "AUTHORIZATION_ALLOWED", "AUTHORIZATION_BLOCKED",
    "APPROVAL_CREATED", "APPROVAL_GRANTED", "APPROVAL_REJECTED",
    "SECURITY_POLICY_CHANGED", "CONFIG_CHANGE_REQUESTED",
    "CONFIG_CHANGE_APPROVED", "REPLAY_DETECTED", "RATE_LIMIT_TRIGGERED",
    "PRIVILEGE_ESCALATION_BLOCKED", "AUDIT_INTEGRITY_FAILURE",
    "INCIDENT_OPENED", "INCIDENT_CLOSED", "BACKUP_CREATED",
    "BACKUP_VERIFIED", "RESTORE_VERIFIED",
}


class TestEventContract:
    def test_all_required_event_types_registered(self):
        assert {e.value for e in EventType} == ALL_EVENT_TYPES

    def test_valid_event_passes(self):
        event = make_event()
        event.validate()

    def test_build_event_validates(self):
        event = build_event(
            event_id=new_identifier("event_id"),
            event_type=EventType.RISK_EVALUATED,
            source="core.risk",
            source_id="risk-engine",
            environment="DEMO",
            correlation_id=new_identifier("correlation_id"),
            event_time=at(12, 0),
            received_time=at(12, 0),
            payload={"risk_decision_id": new_identifier("risk_decision_id")},
        )
        assert event.event_type is EventType.RISK_EVALUATED

    def test_causation_linking(self):
        root = make_event()
        child = make_event(causation_id=root.event_id)
        child.validate()

    def test_to_dict_roundtrip_keys(self):
        data = make_event().to_dict()
        assert set(data) == {
            "event_id", "event_type", "event_version", "event_time", "received_time",
            "source", "source_id", "environment", "correlation_id", "causation_id",
            "entity_id", "payload", "metadata",
        }


class TestEventFailures:
    def test_naive_event_time_rejected(self):
        event = make_event(event_time=datetime(2026, 9, 23, 12, 0))
        with pytest.raises(TimeValidationError):
            event.validate()

    def test_received_before_event_rejected(self):
        event = make_event(event_time=at(12, 5), received_time=at(12, 0))
        with pytest.raises(TimeValidationError):
            event.validate()

    def test_invalid_event_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_event(event_id="evt_short").validate()

    def test_invalid_event_version_rejected(self):
        with pytest.raises(ContractValidationError):
            make_event(event_version="1.0").validate()

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ContractValidationError):
            make_event(event_type="ORDER_TELEPORTED").validate()

    def test_missing_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_event(environment=None).validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_event(environment="PRELIVE").validate()

    def test_missing_correlation_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_event(correlation_id="").validate()

    def test_causation_id_must_be_event_id(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_event(causation_id=new_identifier("order_id")).validate()
        assert excinfo.value.rule_id == "TRACE-002"

    def test_non_mapping_payload_rejected(self):
        with pytest.raises(ContractValidationError):
            make_event(payload=["x"]).validate()

    def test_event_is_immutable(self):
        event = make_event()
        with pytest.raises(FrozenInstanceError):
            event.environment = "LIVE"
