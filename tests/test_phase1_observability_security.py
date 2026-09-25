"""Phase 1 observability + security tests (SECTIONS 32/45)."""
from __future__ import annotations

import json

import pytest

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.observability import (
    LogRecord,
    LogLevel,
    redact_payload_view,
)
from core.data.pipeline import IngestionPipeline, IngestionRequest
from core.data.source_registry import SourceRegistry
from platform.database.sqlite_stores import StorageSet
from platform.monitoring.structured_logger import JsonStructuredLogger
from tests.phase1_factories import at, make_ingestion_request, make_source


class TestLogRecord:
    def _record(self, **overrides):
        defaults = dict(
            timestamp=at(12, 0),
            level=LogLevel.INFO,
            component="core.data.pipeline",
            operation="ingest",
            status="ACCEPTED",
        )
        defaults.update(overrides)
        return LogRecord(**defaults)

    def test_valid_record(self):
        self._record().validate()

    def test_contract_fields_present(self):
        data = self._record(
            correlation_id="cor_1", event_id="evt_1", source_id="feed", duration_ms=1.2
        ).to_dict()
        for key in ("timestamp", "level", "component", "operation", "correlation_id",
                    "event_id", "source_id", "duration_ms", "status", "error_code"):
            assert key in data

    def test_naive_timestamp_rejected(self):
        from datetime import datetime

        with pytest.raises(ContractValidationError):
            self._record(timestamp=datetime(2026, 9, 23, 12, 0)).validate()

    def test_secret_like_detail_key_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            self._record(details={"api_key": "abc"}).validate()
        assert excinfo.value.rule_id == "SEC-001"

    def test_negative_duration_rejected(self):
        with pytest.raises(ContractValidationError):
            self._record(duration_ms=-1).validate()


class TestStructuredLogger:
    def test_json_line_emitted(self):
        lines = []
        logger = JsonStructuredLogger(lines.append)
        logger.log(LogRecord(
            timestamp=at(12, 0), level=LogLevel.INFO, component="c", operation="o",
            status="OK",
        ))
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["component"] == "c"

    def test_sensitive_values_redacted(self):
        lines = []
        logger = JsonStructuredLogger(lines.append, sensitive_fields=("session_key",))
        logger.log(LogRecord(
            timestamp=at(12, 0), level=LogLevel.INFO, component="c", operation="o",
            status="OK", details={"session_key": "super-secret", "symbol": "XAUUSD"},
        ))
        parsed = json.loads(lines[0])
        assert parsed["details"]["session_key"] == "[redacted]"
        assert parsed["details"]["symbol"] == "XAUUSD"

    def test_invalid_record_not_emitted(self):
        lines = []
        logger = JsonStructuredLogger(lines.append)
        from datetime import datetime

        with pytest.raises(ContractValidationError):
            logger.log(LogRecord(
                timestamp=datetime(2026, 9, 23), level=LogLevel.INFO,
                component="c", operation="o", status="OK",
            ))
        assert lines == []


class TestPayloadRedactionBoundary:
    def test_raw_payload_never_transformed_log_view_redacted(self, tmp_path):
        """SECTION 45: sensitive source fields are redacted in logs/views,
        but the raw payload itself is stored untouched."""
        storage = StorageSet(tmp_path / "sec.db")
        registry = SourceRegistry()
        registry.register(make_source(
            source_id="feed-sensitive",
            sensitive_fields=("session_key",),
        ))
        lines = []
        pipeline = IngestionPipeline(
            source_registry=registry, raw_store=storage.raw,
            normalized_store=storage.normalized, lineage_store=storage.lineage,
            event_store=storage.events,
            logger=JsonStructuredLogger(lines.append),
        )
        request = IngestionRequest(**make_ingestion_request(
            source_id="feed-sensitive",
            payload={"symbol": "XAUUSD", "bid": "1", "ask": "2", "session_key": "leak-me"},
        ))
        outcome = pipeline.ingest(request)
        assert outcome.accepted

        raw = storage.raw.get_by_id(outcome.raw_id)
        assert raw.payload["session_key"] == "leak-me"  # raw truth untouched
        parsed_log = json.loads(lines[-1])
        assert "session_key" not in json.dumps(parsed_log)

    def test_no_credentials_in_events_or_raw(self, tmp_path):
        """Events and raw records must never carry secret-like top-level keys."""
        storage = StorageSet(tmp_path / "sec2.db")
        registry = SourceRegistry()
        registry.register(make_source())
        pipeline = IngestionPipeline(
            source_registry=registry, raw_store=storage.raw,
            normalized_store=storage.normalized, lineage_store=storage.lineage,
            event_store=storage.events,
        )
        outcome = pipeline.ingest(IngestionRequest(**make_ingestion_request(
            payload={"symbol": "XAUUSD", "bid": "1", "ask": "2"},
        )))
        assert outcome.accepted
        for event in storage.events.iter_all():
            lowered = {str(k).lower() for k in list(event.payload) + list(event.metadata)}
            assert not any("password" in k or "token" in k or "api_key" in k for k in lowered)


class TestRedactPayloadView:
    def test_view_redaction(self):
        view = redact_payload_view({"token_a": "x", "keep": 1}, ("token_a",))
        assert view == {"token_a": "[redacted]", "keep": 1}
