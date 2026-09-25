"""Intelligence event emission (owned by core.intelligence).

Every material AI operation emits a Phase 1 event (schema-versioned,
immutable, causally linked, environment-aware - SECTION 72). Events are
advisory-plane facts only; no execution semantics exist here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from core.events.contracts import Event, EventType, build_event

CONTRACT_VERSION = "1.0.0"
INTELLIGENCE_SOURCE = "core.intelligence"

#: event_type -> (payload builder hint) ; kept explicit so the validator
#: can verify every Phase 7 event type is actually emitted.
PHASE7_EVENT_TYPES = (
    EventType.FEATURE_CREATED,
    EventType.INTELLIGENCE_DATASET_CREATED,
    EventType.TRAINING_STARTED,
    EventType.TRAINING_COMPLETED,
    EventType.TRAINING_FAILED,
    EventType.MODEL_REGISTERED,
    EventType.MODEL_VALIDATED,
    EventType.MODEL_SUSPENDED,
    EventType.MODEL_RETIRED,
    EventType.INFERENCE_COMPLETED,
    EventType.AI_PROPOSAL_CREATED,
    EventType.DRIFT_DETECTED,
    EventType.MODEL_REPLAYED,
)


def emit(event_type: EventType, *, payload: Mapping[str, Any],
         environment: str, entity_id: str,
         correlation_id: str | None = None,
         causation_id: str | None = None,
         event_time: datetime | None = None,
         received_time: datetime | None = None) -> Event:
    """Build a validated intelligence event in one step."""
    moment = ensure_utc(event_time) if event_time else utc_now()
    return build_event(
        event_type=event_type,
        source=INTELLIGENCE_SOURCE,
        source_id=entity_id,
        environment=environment,
        correlation_id=correlation_id or new_identifier("correlation_id"),
        event_time=moment,
        received_time=ensure_utc(received_time) if received_time
        else moment,
        payload=dict(payload),
        event_id=new_identifier("event_id"),
        causation_id=causation_id,
        entity_id=entity_id,
    )
