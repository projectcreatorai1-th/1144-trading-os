"""Event deduplication (owned by core.data).

Deterministic dedup keys combine source identity, the provider-side event
reference (or payload hash) and the event time - payload hash alone is not
sufficient semantics: the same content at a different time (or from a
different provider reference) is a legitimately separate event (SECTION 22).
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from architecture.contracts.time import canonical
from core.events.contracts import Event
from core.events.store import EventStore

CONTRACT_VERSION = "1.0.0"


def compute_dedup_key(
    *,
    source: str,
    source_id: str,
    payload_hash: str,
    event_time: Any,
    source_event_ref: str | None = None,
) -> str:
    parts = [
        source,
        source_id,
        source_event_ref if source_event_ref is not None else f"hash:{payload_hash}",
        canonical(event_time),
    ]
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class DeduplicationService:
    """Persistent dedup lookup backed by the event store."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def find_duplicate(self, dedup_key: str) -> Event | None:
        return self._store.find_by_dedup_key(dedup_key)

    @staticmethod
    def event_is_duplicate_of(existing: Event, incoming: Mapping[str, Any], dedup_key: str) -> bool:
        """True when the existing event and the incoming request are the same
        logical event (same dedup key). Separates duplicates from same-content
        events at different times, which produce different keys."""
        return existing.metadata.get("dedup_key") == dedup_key
