"""Reconciliation engine (owned by core.reconciliation).

Compares INTERNAL STATE/LEDGER vs EXTERNAL OBSERVATIONS with explicit
tolerances and REPORTS - it never auto-fixes (SECTIONS 22-31, 59). Results
are immutable history. UNKNOWN never becomes MATCH without evidence.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from architecture.contracts.environment import assert_same_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.reconciliation.contracts import (
    CONTRACT_VERSION,
    Difference,
    DifferenceSeverity,
    ExternalObservation,
    ReconciliationResult,
    ReconciliationScope,
    ReconciliationStatus,
)
from core.reconciliation.stores import ObservationStore, ReconciliationStore
from core.reconciliation.tolerance import compare_values, tolerance_for

CONTRACT_VERSION_ENGINE = CONTRACT_VERSION


def _to_decimal(value: Any) -> Decimal | None:
    """Parse a comparison value; None when it cannot be compared (UNKNOWN)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        return None  # binary floats are never compared as financial values
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


class ReconciliationEngine:
    def __init__(self, store: ReconciliationStore, audit=None) -> None:
        self._store = store
        self._audit = audit

    def reconcile_observations(
        self,
        *,
        scope: ReconciliationScope,
        environment: str,
        internal_values: Mapping[str, Any],
        observations: Iterable[ExternalObservation],
        comparison_time: datetime,
        expected_observation_schema: str | None = None,
        stale_before: datetime | None = None,
        internal_version: str = "current",
        correlation_id: str | None = None,
    ) -> ReconciliationResult:
        """Compare internal values against the latest payload of each observed
        entity. Late/stale observations and unknown values are classified
        UNKNOWN - never silently matched (RECON-002)."""
        comparison_time = ensure_utc(comparison_time, location="reconciliation.comparison_time")
        external: dict[str, Any] = {}
        unknown_items: list[str] = []
        excluded_keys: set[str] = set()
        for observation in observations:
            observation.validate()
            assert_same_environment(
                environment, observation.environment,
                context="reconciliation.observation_environment",
            )
            if expected_observation_schema is not None and observation.schema_version != expected_observation_schema:
                unknown_items.append(f"{observation.entity_id}:schema_mismatch")
                excluded_keys.add(observation.entity_id)
                continue
            if stale_before is not None and observation.observed_at < ensure_utc(
                stale_before, location="reconciliation.stale_before"
            ):
                unknown_items.append(f"{observation.entity_id}:stale_observation")
                excluded_keys.add(observation.entity_id)
                continue
            if observation.entity_id in external:
                unknown_items.append(f"{observation.entity_id}:duplicate_external")
                excluded_keys.add(observation.entity_id)
                continue
            external[observation.entity_id] = observation.payload.get("value")

        return self.compare_values_map(
            scope=scope,
            environment=environment,
            internal_values=internal_values,
            external_values=external,
            unknown_items=tuple(unknown_items),
            excluded_keys=frozenset(excluded_keys),
            source="external-observations",
            internal_version=internal_version,
            external_version="observations",
            comparison_time=comparison_time,
            correlation_id=correlation_id,
        )

    def compare_values_map(
        self,
        *,
        scope: ReconciliationScope,
        environment: str,
        internal_values: Mapping[str, Any],
        external_values: Mapping[str, Any],
        source: str,
        comparison_time: datetime,
        internal_version: str = "current",
        external_version: str = "external",
        unknown_items: tuple[str, ...] = (),
        excluded_keys: frozenset[str] = frozenset(),
        correlation_id: str | None = None,
    ) -> ReconciliationResult:
        tolerance = tolerance_for(scope.value)
        matched: list[str] = []
        mismatches: list[Difference] = []
        missing_internal: list[str] = []
        missing_external: list[str] = []
        unknown = list(unknown_items)

        internal_keys = list(internal_values)
        internal_seen: set[str] = set()
        for key in internal_keys:
            if key in internal_seen:
                unknown.append(f"{key}:duplicate_internal")
                continue
            internal_seen.add(key)
            if key in excluded_keys:
                continue  # already classified UNKNOWN via evidence
            if key not in external_values:
                missing_external.append(key)
                continue
            internal_decimal = _to_decimal(internal_values[key])
            external_decimal = _to_decimal(external_values[key])
            if internal_decimal is None or external_decimal is None:
                unknown.append(f"{key}:uncomparable_value")
                continue
            difference = compare_values(
                field=key,
                internal=internal_decimal,
                external=external_decimal,
                tolerance=tolerance,
            )
            if difference.severity is DifferenceSeverity.INFO:
                matched.append(key)
            else:
                mismatches.append(difference)

        for key in external_values:
            if key in internal_seen or key in excluded_keys:
                continue
            missing_internal.append(key)

        if internal_seen and set(internal_seen) & set(external_values) and (
            missing_external or missing_internal
        ) and matched:
            status = ReconciliationStatus.PARTIAL
        elif matched and not (mismatches or missing_external or missing_internal or unknown):
            status = ReconciliationStatus.MATCH
        elif missing_internal and not (matched or mismatches or missing_external):
            status = ReconciliationStatus.MISSING_INTERNAL
        elif missing_external and not (matched or mismatches or missing_internal):
            status = ReconciliationStatus.MISSING_EXTERNAL
        elif not matched and not mismatches and not missing_internal and not missing_external and unknown:
            status = ReconciliationStatus.UNKNOWN
        else:
            status = ReconciliationStatus.MISMATCH

        result = ReconciliationResult(
            reconciliation_id=new_identifier("reconciliation_id"),
            scope=scope,
            status=status,
            timestamp=comparison_time,
            environment=environment,
            source=source,
            correlation_id=correlation_id or new_identifier("correlation_id"),
            internal_version=internal_version,
            external_version=external_version,
            tolerance=tolerance.to_dict(),
            difference_summary={
                "matched": len(matched),
                "mismatched": len(mismatches),
                "missing_internal": len(missing_internal),
                "missing_external": len(missing_external),
                "unknown": len(unknown),
            },
            matched_items=tuple(sorted(matched)),
            mismatched_items=tuple(mismatches),
            missing_internal=tuple(sorted(missing_internal)),
            missing_external=tuple(sorted(missing_external)),
            unknown_items=tuple(unknown),
        )
        result.validate()
        self._store.append(result)
        if self._audit is not None and status is not ReconciliationStatus.MATCH:
            from platform.audit.contracts import ActorType, AuditRecord

            self._audit.append(AuditRecord(
                audit_id=new_identifier("audit_id"),
                actor_type=ActorType.SYSTEM,
                actor_id="core.reconciliation.engine",
                action="RECONCILIATION_MISMATCH",
                entity_type="reconciliation",
                entity_id=result.reconciliation_id,
                event_time=comparison_time,
                before={"internal_version": internal_version},
                after={"status": status.value,
                       "summary": dict(result.difference_summary)},
                reason=f"{scope.value} reconciliation status {status.value}",
                source="core.reconciliation.engine",
                environment=environment,
                correlation_id=result.correlation_id,
            ))
        return result
