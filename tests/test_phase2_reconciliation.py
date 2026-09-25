"""Phase 2 reconciliation tests: contract, tolerance, all 14 scenarios
(SECTIONS 22-31, 48)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from architecture.contracts.errors import ContractValidationError, EnvironmentMismatchError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.provenance import Provenance
from core.reconciliation.contracts import (
    Difference,
    DifferenceSeverity,
    ExternalObservation,
    ReconciliationScope,
    ReconciliationStatus,
    compute_observation_hash,
)
from core.reconciliation.engine import ReconciliationEngine
from core.reconciliation.tolerance import compare_values, tolerance_for
from platform.database.sqlite_stores import StorageSet
from tests.phase1_factories import at


@pytest.fixture()
def storage(tmp_path):
    storage = StorageSet(tmp_path / "recon.db")
    yield storage
    storage.close()


def make_observation(entity_id: str, value, *, environment="PAPER", observed_at=None,
                     received_at=None, schema_version="1.0.0", payload_extra=None,
                     source="broker-statement") -> ExternalObservation:
    payload = {"value": value}
    if payload_extra:
        payload.update(payload_extra)
    observed = observed_at or at(12, 0)
    received = received_at or observed
    observation = ExternalObservation(
        observation_id=new_identifier("observation_id"),
        source=source,
        entity_type="account_balance",
        entity_id=entity_id,
        observed_at=observed,
        received_at=received,
        environment=environment,
        payload=payload,
        payload_hash=compute_observation_hash(payload),
        schema_version=schema_version,
        provenance=Provenance(
            source=source, source_id=entity_id,
            ingestion_time=received, event_time=observed,
        ),
        correlation_id=new_identifier("correlation_id"),
    )
    observation.validate()
    return observation


def run(storage, internal, observations, **overrides):
    engine = ReconciliationEngine(storage.reconciliations, storage.audit)
    defaults = dict(
        scope=ReconciliationScope.BALANCE,
        environment="PAPER",
        internal_values=internal,
        observations=observations,
        comparison_time=at(12, 5),
    )
    defaults.update(overrides)
    return engine.reconcile_observations(**defaults)


class TestObservationContract:
    def test_valid_observation(self):
        make_observation("ACC-1", "1000.00")

    def test_immutable(self):
        observation = make_observation("ACC-1", "1")
        with pytest.raises(FrozenInstanceError):
            observation.payload = {}

    def test_hash_verified(self):
        payload = {"value": "5"}
        with pytest.raises(ContractValidationError):
            ExternalObservation(
                observation_id=new_identifier("observation_id"), source="s",
                entity_type="t", entity_id="e", observed_at=at(12), received_at=at(12),
                environment="PAPER", payload=payload, payload_hash="0" * 64,
                schema_version="1.0.0",
                provenance=Provenance(source="s", source_id="e",
                                      ingestion_time=at(12), event_time=at(12)),
                correlation_id=new_identifier("correlation_id"),
            ).validate()

    def test_store_roundtrip(self, storage):
        observation = make_observation("ACC-1", "7.25")
        storage.observations.append(observation)
        loaded = storage.observations.get_by_id(observation.observation_id)
        assert loaded.payload == observation.payload
        assert list(storage.observations.iter_by_entity("account_balance", "ACC-1"))


class TestTolerance:
    def test_registry_scopes(self):
        assert tolerance_for("BALANCE").absolute == Decimal("0.01")
        assert tolerance_for("ORDER").absolute == Decimal("0")
        assert tolerance_for("NO_SCOPE_FALLBACK").absolute == Decimal("0.00000001")

    def test_compare_within_tolerance_info(self):
        tolerance = tolerance_for("BALANCE")
        difference = compare_values(
            field="balance", internal=Decimal("1000.005"),
            external=Decimal("1000.00"), tolerance=tolerance,
        )
        assert difference.severity is DifferenceSeverity.INFO

    def test_compare_beyond_tolerance_error(self):
        difference = compare_values(
            field="balance", internal=Decimal("1000.00"),
            external=Decimal("995.00"), tolerance=tolerance_for("BALANCE"),
        )
        assert difference.severity is DifferenceSeverity.ERROR
        assert difference.absolute_difference == "5.00"

    def test_tolerance_boundary_exact(self):
        # exactly at the absolute tolerance boundary -> within (<=)
        difference = compare_values(
            field="balance", internal=Decimal("1000.01"),
            external=Decimal("1000.00"), tolerance=tolerance_for("BALANCE"),
        )
        assert difference.severity is DifferenceSeverity.INFO
        beyond = compare_values(
            field="balance", internal=Decimal("1000.02"),
            external=Decimal("1000.00"), tolerance=tolerance_for("BALANCE"),
        )
        assert beyond.severity is DifferenceSeverity.ERROR

    def test_relative_tolerance_blocks_large_relative_diff(self):
        # tiny absolute, huge relative
        difference = compare_values(
            field="amount", internal=Decimal("1.00"),
            external=Decimal("0.00"), tolerance=tolerance_for("TRANSACTION"),
        )
        assert difference.severity is DifferenceSeverity.ERROR
        assert difference.relative_difference == "undefined"


class TestFourteenScenarios:
    def test_01_exact_match(self, storage):
        result = run(storage, {"ACC-1": "1000.00"}, [make_observation("ACC-1", "1000.00")])
        assert result.status is ReconciliationStatus.MATCH

    def test_02_amount_mismatch(self, storage):
        result = run(storage, {"ACC-1": "1000.00"}, [make_observation("ACC-1", "995.00")])
        assert result.status is ReconciliationStatus.MISMATCH
        assert result.mismatched_items[0].absolute_difference == "5.00"

    def test_03_quantity_mismatch(self, storage):
        result = run(
            storage, {"POS-1": "0.10000000"}, [make_observation("POS-1", "0.20000000")],
            scope=ReconciliationScope.POSITION,
        )
        assert result.status is ReconciliationStatus.MISMATCH

    def test_04_missing_internal(self, storage):
        result = run(storage, {}, [make_observation("ACC-9", "10.00")])
        assert result.status is ReconciliationStatus.MISSING_INTERNAL
        assert result.missing_internal == ("ACC-9",)

    def test_05_missing_external(self, storage):
        result = run(storage, {"ACC-1": "10.00"}, [])
        assert result.status is ReconciliationStatus.MISSING_EXTERNAL
        assert result.missing_external == ("ACC-1",)

    def test_06_duplicate_external(self, storage):
        result = run(
            storage, {"ACC-1": "10.00"},
            [make_observation("ACC-1", "10.00"), make_observation("ACC-1", "10.00")],
        )
        assert "ACC-1:duplicate_external" in result.unknown_items

    def test_07_duplicate_internal(self, storage):
        # Mapping inputs cannot carry duplicate keys; callers that detect
        # duplicate internal items classify them explicitly as UNKNOWN.
        engine = ReconciliationEngine(storage.reconciliations, storage.audit)
        result = engine.compare_values_map(
            scope=ReconciliationScope.BALANCE, environment="PAPER",
            internal_values={"ACC-1": "10.00"},
            external_values={"ACC-1": "10.00"},
            source="internal", comparison_time=at(12, 5),
            unknown_items=("ACC-1:duplicate_internal",),
        )
        assert result.status is not ReconciliationStatus.MATCH
        assert "ACC-1:duplicate_internal" in result.unknown_items

    def test_08_late_observation_never_matched(self, storage):
        # observation arrived comparing an old moment: stale_before marks it UNKNOWN
        result = run(
            storage, {"ACC-1": "1000.00"},
            [make_observation("ACC-1", "1000.00", observed_at=at(10, 0), received_at=at(12, 4))],
            stale_before=at(11, 0),
        )
        assert "ACC-1:stale_observation" in result.unknown_items
        assert result.status is not ReconciliationStatus.MATCH

    def test_09_stale_observation(self, storage):
        result = run(
            storage, {"ACC-1": "1000.00"},
            [make_observation("ACC-1", "900.00", observed_at=at(9, 0))],
            stale_before=at(11, 0),
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert result.unknown_items

    def test_10_unknown_observation_value(self, storage):
        result = run(storage, {"ACC-1": "1000.00"}, [make_observation("ACC-1", "unavailable")])
        assert "ACC-1:uncomparable_value" in result.unknown_items
        assert result.status is ReconciliationStatus.UNKNOWN

    def test_11_rounding_difference_within_tolerance(self, storage):
        # broker rounds to cents differently by a fraction of a cent
        result = run(storage, {"ACC-1": "1000.004"}, [make_observation("ACC-1", "1000.00")])
        assert result.status is ReconciliationStatus.MATCH
        assert result.matched_items == ("ACC-1",)

    def test_12_tolerance_boundary(self, storage):
        result_inside = run(storage, {"ACC-1": "1000.01"}, [make_observation("ACC-1", "1000.00")])
        assert result_inside.status is ReconciliationStatus.MATCH
        result_outside = run(storage, {"ACC-1": "1000.02"}, [make_observation("ACC-1", "1000.00")])
        assert result_outside.status is ReconciliationStatus.MISMATCH

    def test_13_environment_mismatch_fails_closed(self, storage):
        with pytest.raises(EnvironmentMismatchError):
            run(storage, {"ACC-1": "1.00"}, [make_observation("ACC-1", "1.00", environment="LIVE")])

    def test_14_schema_version_mismatch_unknown(self, storage):
        result = run(
            storage, {"ACC-1": "1000.00"},
            [make_observation("ACC-1", "1000.00", schema_version="9.9.9")],
            expected_observation_schema="1.0.0",
        )
        assert "ACC-1:schema_mismatch" in result.unknown_items
        assert result.status is ReconciliationStatus.UNKNOWN

    def test_partial_mixed_result(self, storage):
        result = run(
            storage, {"ACC-1": "100.00", "ACC-2": "50.00"},
            [make_observation("ACC-1", "100.00")],
        )
        assert result.status is ReconciliationStatus.PARTIAL
        assert result.matched_items == ("ACC-1",)
        assert result.missing_external == ("ACC-2",)


class TestNoAutoFix:
    def test_mismatch_reported_not_corrected(self, storage):
        """SECTION 59: Internal 1000 vs External 995 -> MISMATCH report;
        internal values are never rewritten."""
        internal = {"ACC-1": "1000.00"}
        result = run(storage, internal, [make_observation("ACC-1", "995.00")])
        assert result.status is ReconciliationStatus.MISMATCH
        assert internal == {"ACC-1": "1000.00"}  # untouched
        audits = list(storage.audit.iter_by_correlation_id(result.correlation_id))
        assert any(a.action == "RECONCILIATION_MISMATCH" for a in audits)

    def test_result_history_immutable(self, storage):
        result = run(storage, {"ACC-1": "1.00"}, [make_observation("ACC-1", "1.00")])
        stored = storage.reconciliations.get_by_id(result.reconciliation_id)
        assert stored.status is ReconciliationStatus.MATCH
        with pytest.raises(FrozenInstanceError):
            stored.status = ReconciliationStatus.MISMATCH

    def test_match_requires_zero_issues(self, storage):
        with pytest.raises(ContractValidationError) as excinfo:
            # engineer a MATCH with differences -> contract rejects (RECON-002)
            from core.reconciliation.contracts import ReconciliationResult

            ReconciliationResult(
                reconciliation_id=new_identifier("reconciliation_id"),
                scope=ReconciliationScope.BALANCE,
                status=ReconciliationStatus.MATCH,
                timestamp=at(12, 5), environment="PAPER", source="s",
                correlation_id=new_identifier("correlation_id"),
                internal_version="current", external_version="external",
                tolerance={"absolute": "0.01", "relative": "0.0001"},
                difference_summary={"matched": 0},
                matched_items=(),
                mismatched_items=(Difference(
                    field="x", internal_value="1", external_value="2", difference="-1",
                    absolute_difference="1", relative_difference="0.5", tolerance="0.01",
                    severity=DifferenceSeverity.ERROR, reason="beyond tolerance",
                ),),
                missing_internal=(), missing_external=(), unknown_items=(),
            ).validate()
        assert excinfo.value.rule_id == "RECON-002"
