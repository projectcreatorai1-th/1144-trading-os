"""Audit contract tests (SECTION 16) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import (
    ContractValidationError,
    IdentifierValidationError,
)
from platform.audit.contracts import ActorType
from tests.factories import make_audit_record


class TestAuditContract:
    def test_valid_record(self):
        make_audit_record().validate()

    def test_actor_types(self):
        assert {t.value for t in ActorType} == {"USER", "SYSTEM", "STRATEGY", "AI_MODEL"}

    def test_record_answers_audit_questions(self):
        record = make_audit_record()
        data = record.to_dict()
        for key in (
            "actor_type", "actor_id", "action", "entity_type", "entity_id",
            "event_time", "before", "after", "reason", "environment",
            "correlation_id", "policy_version", "risk_version", "model_version",
        ):
            assert key in data

    def test_versioned_audit_context(self):
        make_audit_record(
            policy_version="1.2.0", risk_version="1.0.0", model_version="regime-1.0.0"
        ).validate()


class TestAuditFailures:
    def test_without_before_and_after_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_audit_record(before=None, after=None).validate()
        assert excinfo.value.rule_id == "TRACE-001"

    def test_missing_correlation_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_audit_record(correlation_id="").validate()

    def test_invalid_actor_type_rejected(self):
        with pytest.raises(ContractValidationError):
            make_audit_record(actor_type="ROBOT").validate()

    def test_missing_reason_rejected(self):
        with pytest.raises(ContractValidationError):
            make_audit_record(reason="").validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_audit_record(environment="TRAINING").validate()

    def test_invalid_audit_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_audit_record(audit_id="audit-1").validate()

    def test_audit_record_immutable(self):
        record = make_audit_record()
        with pytest.raises(FrozenInstanceError):
            record.action = "ORDER_DELETED"
