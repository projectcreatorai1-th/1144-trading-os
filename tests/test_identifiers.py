"""Global identifier standard tests (SECTION 7)."""
from __future__ import annotations

import re

import pytest

from architecture.contracts.errors import IdentifierValidationError
from architecture.contracts.identifiers import (
    identifier_kinds,
    is_valid_any_identifier,
    is_valid_identifier,
    new_identifier,
    prefix_for,
    validate_any_identifier,
    validate_identifier,
)

EXPECTED_KINDS = {
    "event_id", "decision_id", "policy_id", "risk_decision_id", "order_id",
    "execution_id", "position_id", "ledger_entry_id", "audit_id", "request_id",
    "correlation_id", "causation_id", "session_id", "strategy_id", "model_id",
    "model_version_id", "config_id", "user_id",
    # Phase 1 additions (identifiers registry 1.1.0)
    "raw_id", "normalized_id", "lineage_id", "ingestion_id",
    # Phase 2 additions (identifiers registry 1.2.0)
    "state_id", "snapshot_id", "transition_id", "observation_id", "reconciliation_id",
    # Phase 3 additions (identifiers registry 1.3.0)
    "policy_evaluation_id", "risk_context_id", "risk_budget_id",
    # Phase 4 additions (identifiers registry 1.4.0)
    "intent_id", "strategy_evaluation_id", "portfolio_id",
    "capital_allocation_id", "portfolio_decision_id", "capability_profile_id",
    # Phase 5 additions (identifiers registry 1.5.0)
    "outbox_message_id",
    # Phase 6 additions (identifiers registry 1.6.0)
    "dataset_id", "research_run_id", "research_result_id",
    "strategy_candidate_id", "execution_model_id", "research_artifact_id",
    "bias_report_id", "stress_report_id",
    # Phase 7 intelligence kinds (identifiers registry 1.7.0)
    "feature_definition_id", "feature_snapshot_id", "intelligence_dataset_id",
    "label_definition_id", "training_run_id", "model_evaluation_id",
    "model_validation_id", "inference_id", "ai_output_id", "ai_proposal_id",
    "explanation_id", "drift_report_id", "model_replay_id", "model_health_id",
    "model_selection_id", "ai_incident_id",
    # Phase 8 security kinds (identifiers registry 1.8.0)
    "credential_id", "secret_id", "key_id", "approval_id",
    "governance_transition_id", "security_incident_id", "backup_id",
    "restore_id",
    # Phase 10 connectivity kinds (identifiers registry 1.9.0)
    "connection_id", "feed_provider_id",
    # Phase 11/12 observability + incident kinds (identifiers registry 1.10.0)
    "alert_id", "operational_incident_id",
    # Gateway server session kind (identifiers registry 1.11.0)
    "gateway_session_id",
}


class TestIdentifierStandard:
    def test_all_required_kinds_registered(self):
        assert EXPECTED_KINDS == set(identifier_kinds())

    @pytest.mark.parametrize("kind", sorted(EXPECTED_KINDS))
    def test_generation_and_validation_roundtrip(self, kind):
        value = new_identifier(kind)
        assert is_valid_identifier(kind, value)
        assert validate_identifier(kind, value) == value

    def test_format_matches_registry_regex(self):
        value = new_identifier("event_id")
        assert re.match(r"^[a-z]{3}_[0-9a-f]{32}$", value)

    def test_prefixes_are_three_letters(self):
        for kind in identifier_kinds():
            assert len(prefix_for(kind)) == 3

    def test_any_identifier_accepts_all_kinds(self):
        for kind in sorted(EXPECTED_KINDS):
            assert is_valid_any_identifier(new_identifier(kind))


class TestIdentifierFailures:
    def test_unknown_kind_rejected(self):
        with pytest.raises(IdentifierValidationError):
            new_identifier("trade_id")

    def test_wrong_prefix_rejected(self):
        with pytest.raises(IdentifierValidationError):
            validate_identifier("event_id", new_identifier("order_id"))

    def test_short_suffix_rejected(self):
        with pytest.raises(IdentifierValidationError):
            validate_identifier("event_id", "evt_deadbeef")

    def test_non_string_rejected(self):
        with pytest.raises(IdentifierValidationError):
            validate_identifier("event_id", 12345)

    def test_empty_rejected(self):
        with pytest.raises(IdentifierValidationError):
            validate_identifier("event_id", "")

    def test_correlation_must_be_canonical(self):
        with pytest.raises(IdentifierValidationError):
            validate_any_identifier("not-an-id")

    def test_uppercase_hex_rejected(self):
        value = "evt_" + "A" * 32
        with pytest.raises(IdentifierValidationError):
            validate_identifier("event_id", value)
