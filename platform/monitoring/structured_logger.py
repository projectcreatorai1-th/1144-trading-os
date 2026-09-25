"""Structured logging implementation (owned by platform.monitoring).

Implements the kernel Logger port: JSON lines with timestamp, level,
component, operation, correlation_id, event_id, source_id, duration, status,
error_code. Logs are observability output only - never a source of truth -
and secrets/sensitive fields are redacted before emission (SECTION 32/45).
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Callable, TextIO

from architecture.contracts.observability import LogRecord

CONTRACT_VERSION = "1.0.0"


def _stdlib_sink(line: str) -> None:
    logging.getLogger("1144.trading.os").info(line)


class JsonStructuredLogger:
    """Validates, redacts and emits one JSON object per log record."""

    def __init__(
        self,
        sink: Callable[[str], None] | None = None,
        *,
        sensitive_fields: tuple[str, ...] = (),
    ) -> None:
        self._sink = sink or _stdlib_sink
        self._sensitive_fields = sensitive_fields

    def with_sensitive_fields(self, sensitive_fields: tuple[str, ...]) -> "JsonStructuredLogger":
        return JsonStructuredLogger(self._sink, sensitive_fields=sensitive_fields)

    def log(self, record: LogRecord) -> None:
        record.validate()
        redacted = record.redacted(self._sensitive_fields)
        self._sink(json.dumps(redacted.to_dict(), sort_keys=True, ensure_ascii=False))


class StreamSink:
    """Writes log lines to a stream (stderr by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr

    def __call__(self, line: str) -> None:
        self._stream.write(line + "\n")
