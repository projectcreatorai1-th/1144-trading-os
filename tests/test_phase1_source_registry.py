"""Phase 1 data source registry tests (SECTION 6)."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import ContractError, IngestionError
from core.data.contracts import SourceStatus, SourceType, TimestampSemantics
from core.data.source_registry import SourceRegistry
from tests.phase1_factories import make_calendar_source, make_source


@pytest.fixture()
def registry() -> SourceRegistry:
    registry = SourceRegistry()
    registry.register(make_source())
    registry.register(make_calendar_source())
    return registry


class TestRegistration:
    def test_register_and_lookup(self, registry: SourceRegistry):
        record = registry.require("feed-xauusd")
        assert record.source_type is SourceType.MARKET_DATA
        assert registry.count() == 2

    def test_duplicate_registration_rejected(self, registry: SourceRegistry):
        with pytest.raises(ContractError) as excinfo:
            registry.register(make_source())
        assert excinfo.value.rule_id == "ARCH-003"

    def test_unknown_schema_rejected(self):
        bad = make_source(schema_id="does_not_exist")
        with pytest.raises(ContractError) as excinfo:
            SourceRegistry().register(bad)
        assert excinfo.value.rule_id == "DQ-013"

    def test_missing_reliability_metadata_rejected(self):
        bad = make_source(reliability_metadata={"stale_threshold_seconds": 10})
        with pytest.raises(ContractError):
            SourceRegistry().register(bad)

    def test_secret_like_metadata_rejected(self):
        bad = make_source(reliability_metadata={
            "stale_threshold_seconds": 10, "future_tolerance_seconds": 5, "api_key": "x"
        })
        with pytest.raises(ContractError) as excinfo:
            SourceRegistry().register(bad)
        assert excinfo.value.rule_id == "SEC-001"

    def test_invalid_timezone_string_rejected_later_by_time_use(self, registry: SourceRegistry):
        # timezone is carried as declared metadata; structural validation applies
        record = make_source(source_id="feed-tz", timezone="Not/AZone")
        registry.register(record)
        assert registry.get("feed-tz").timezone == "Not/AZone"


class TestRequire:
    def test_unknown_source_fails_closed(self, registry: SourceRegistry):
        with pytest.raises(IngestionError) as excinfo:
            registry.require("no-such-source")
        assert excinfo.value.rule_id == "DQ-012"

    def test_disabled_source_fails_closed(self, registry: SourceRegistry):
        registry.register(make_source(source_id="feed-off", enabled=False))
        with pytest.raises(IngestionError):
            registry.require("feed-off")

    def test_retired_source_fails_closed(self, registry: SourceRegistry):
        registry.register(
            make_source(source_id="feed-old", status=SourceStatus.RETIRED)
        )
        with pytest.raises(IngestionError):
            registry.require("feed-old")

    def test_all_types_registerable(self):
        registry = SourceRegistry()
        for index, source_type in enumerate(SourceType):
            registry.register(
                make_source(source_id=f"src-{source_type.value}", source_type=source_type)
            )
        assert registry.count() == len(list(SourceType))

    def test_timestamp_semantics_enum(self):
        assert {s.value for s in TimestampSemantics} == {"REALTIME", "DELAYED", "BATCH", "HISTORICAL"}
