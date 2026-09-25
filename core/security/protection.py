"""Request security: replay protection + rate limiting + audit integrity
+ backup/restore security + incidents + security events + observability
(owned by core.security).

SECTION 20/21/25/36/37/38/22/50. All deterministic, all fail-closed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc

from core.events.contracts import Event, EventType, build_event
from core.security.contracts import (
    IntegrityStatus,
    IncidentSeverity,
    RateLimitDecision,
    RateLimitVerdict,
    ReplayVerdict,
    SecurityIncident,
    SecurityIncidentStatus,
    SecurityRequest,
    BackupManifest,
    canonical_hash,
)
from platform.audit.contracts import AuditRecord

CONTRACT_VERSION = "1.0.0"
SECURITY_SOURCE = "core.security"
REQUEST_MAX_AGE = timedelta(minutes=10)
INCIDENT_MACHINE = "incident_lifecycle"


class ProtectionError(ContractError):
    rule_id = "SEC-PROTECTION"


# --------------------------------------------------------------------- #
# Replay protection + idempotency                                       #
# --------------------------------------------------------------------- #
class ReplayGuard:
    """SECTION 20: same request id + same semantics -> IDEMPOTENT_REPEAT;
    same id + different semantics -> CORRUPTION_SUSPECTED; stale -> BLOCK."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}
        self._detections = 0

    @property
    def detections(self) -> int:
        return self._detections

    def check(self, request: SecurityRequest, *, now: datetime) -> \
            tuple[ReplayVerdict, SecurityRequest | None]:
        moment = ensure_utc(now, location="replay.now")
        if moment - ensure_utc(request.timestamp) > REQUEST_MAX_AGE:
            self._detections += 1
            return ReplayVerdict.REPLAY_DETECTED, None
        fingerprint = request.fingerprint()
        previous = self._seen.get(request.request_id)
        if previous is None:
            self._seen[request.request_id] = fingerprint
            return ReplayVerdict.FIRST_SEEN, None
        if previous == fingerprint:
            self._detections += 1
            return ReplayVerdict.IDEMPOTENT_REPEAT, None
        self._detections += 1
        return ReplayVerdict.CORRUPTION_SUSPECTED, None

    def require_first_use(self, request: SecurityRequest, *,
                          now: datetime) -> ReplayVerdict:
        verdict, _ = self.check(request, now=now)
        if verdict is ReplayVerdict.CORRUPTION_SUSPECTED:
            raise ProtectionError(
                "request id reused with different semantics (corruption)",
                location="replay.guard", rule_id="SEC-REPLAY",
                details={"request_id": request.request_id})
        if verdict is ReplayVerdict.REPLAY_DETECTED:
            raise ProtectionError(
                "stale privileged request rejected",
                location="replay.guard", rule_id="SEC-REPLAY")
        return verdict


# --------------------------------------------------------------------- #
# Rate limiting                                                          #
# --------------------------------------------------------------------- #
class RateLimiter:
    """Deterministic fixed-window counters. Rate limiting is an auditable
    guardrail, NEVER a substitute for authorization (SECTION 21)."""

    def __init__(self, *, max_operations: int = 10,
                 window_seconds: int = 60) -> None:
        self._max = max_operations
        self._window = window_seconds
        self._counts: dict[str, tuple[datetime, int]] = {}
        self._triggered = 0

    @property
    def triggered(self) -> int:
        return self._triggered

    def check(self, *, limit_key: str, now: datetime) -> RateLimitDecision:
        moment = ensure_utc(now, location="ratelimit.now")
        window_start = moment.replace(
            microsecond=(moment.microsecond // 1_000_000) * 1_000_000)
        current = self._counts.get(limit_key)
        if current is None or \
                (moment - current[0]).total_seconds() >= self._window:
            used = 0
        else:
            used = current[1]
        if used >= self._max:
            self._triggered += 1
            decision = RateLimitDecision(
                limit_key=limit_key, verdict=RateLimitVerdict.EXCEEDED,
                window_start=window_start, window_seconds=self._window,
                max_operations=self._max, operations_used=used,
                decided_at=moment)
            decision.validate()
            return decision
        self._counts[limit_key] = (moment, used + 1)
        decision = RateLimitDecision(
            limit_key=limit_key, verdict=RateLimitVerdict.ALLOWED,
            window_start=window_start, window_seconds=self._window,
            max_operations=self._max, operations_used=used + 1,
            decided_at=moment)
        decision.validate()
        return decision

    def require_allowed(self, *, limit_key: str, now: datetime) -> None:
        decision = self.check(limit_key=limit_key, now=now)
        if decision.verdict is RateLimitVerdict.EXCEEDED:
            raise ProtectionError(
                "rate limit exceeded",
                location="ratelimit.require", rule_id="SEC-RATELIMIT",
                details={"limit_key": limit_key,
                         "max": decision.max_operations})


# --------------------------------------------------------------------- #
# Audit integrity (tamper-evident chain over the ONE audit path)         #
# --------------------------------------------------------------------- #
class AuditChain:
    """Chained integrity over the existing AuditRepository contract
    (audit_record 1.1.0 optional fields). This is the one audit authority
    extension - not a second store: it appends through the SAME port."""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._by_id: dict[str, AuditRecord] = {}
        self._head: str = "0" * 64
        self._integrity_failures = 0

    @property
    def head(self) -> str:
        return self._head

    @property
    def integrity_failures(self) -> int:
        return self._integrity_failures

    def append(self, record: AuditRecord) -> AuditRecord:
        record.validate()
        integrity = canonical_hash({
            "previous_hash": self._head,
            "record": {
                "audit_id": record.audit_id,
                "actor_type": record.actor_type.value,
                "actor_id": record.actor_id,
                "action": record.action,
                "entity_type": record.entity_type,
                "entity_id": record.entity_id,
                "event_time": ensure_utc(record.event_time).isoformat(),
                "environment": record.environment,
                "before": record.before,
                "after": record.after,
                "reason": record.reason,
            },
        })
        sealed = replace(record, integrity_hash=integrity,
                         previous_hash=self._head)
        self._records.append(sealed)
        self._by_id[sealed.audit_id] = sealed
        self._head = integrity
        return sealed

    def verify(self) -> IntegrityStatus:
        """Walk the whole chain: any mutation of actor/timestamp/operation/
        environment/result/causation/previous-hash/payload is detected."""
        previous = "0" * 64
        for record in self._records:
            if record.previous_hash != previous:
                self._integrity_failures += 1
                return IntegrityStatus.CORRUPTED
            expected = canonical_hash({
                "previous_hash": previous,
                "record": {
                    "audit_id": record.audit_id,
                    "actor_type": record.actor_type.value,
                    "actor_id": record.actor_id,
                    "action": record.action,
                    "entity_type": record.entity_type,
                    "entity_id": record.entity_id,
                    "event_time": ensure_utc(record.event_time).isoformat(),
                    "environment": record.environment,
                    "before": record.before,
                    "after": record.after,
                    "reason": record.reason,
                },
            })
            if record.integrity_hash != expected:
                self._integrity_failures += 1
                return IntegrityStatus.CORRUPTED
            previous = record.integrity_hash or previous
        return IntegrityStatus.VERIFIED

    def require_verified(self) -> None:
        """SECTION 41: audit integrity failure BLOCKS privileged ops."""
        if self.verify() is not IntegrityStatus.VERIFIED:
            raise ProtectionError(
                "audit chain integrity failure: privileged operations are "
                "blocked until the chain verifies",
                location="audit.chain", rule_id="SEC-AUDIT-INTEGRITY")

    def __iter__(self):
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def get_by_id(self, audit_id: str) -> AuditRecord:
        return self._by_id[audit_id]


def forge_record(record: AuditRecord, **changes: Any) -> AuditRecord:
    """Test-only helper: produce a tampered copy (never used in prod)."""
    return replace(record, **changes)


# --------------------------------------------------------------------- #
# Backup + restore security                                              #
# --------------------------------------------------------------------- #
class BackupSecurityService:
    """SECTION 37/38: backups carry integrity evidence; corrupt or UNKNOWN
    backups are never restored."""

    def __init__(self) -> None:
        self._manifests: dict[str, BackupManifest] = {}

    def create_backup(self, *, environment: str, source_version: str,
                      components: Mapping[str, Any], at: datetime) -> BackupManifest:
        if not components:
            raise ProtectionError(
                "a backup must cover at least one store",
                location="backup.create", rule_id="SEC-BACKUP")
        component_hashes = {
            name: canonical_hash(value) for name, value in components.items()}
        manifest = BackupManifest(
            backup_id=new_identifier("backup_id"),
            created_at=ensure_utc(at, location="backup.at"),
            environment=environment, source_version=source_version,
            component_hashes=component_hashes, content_hash="",
            manifest_hash="", integrity_status=IntegrityStatus.UNKNOWN)
        object.__setattr__(manifest, "content_hash",
                           manifest.compute_content_hash())
        object.__setattr__(manifest, "manifest_hash", canonical_hash({
            "backup_id": manifest.backup_id,
            "content_hash": manifest.content_hash,
            "integrity_status": IntegrityStatus.UNKNOWN.value,
        }))
        verified = replace(manifest, integrity_status=IntegrityStatus.VERIFIED)
        object.__setattr__(verified, "manifest_hash", canonical_hash({
            "backup_id": verified.backup_id,
            "content_hash": verified.content_hash,
            "integrity_status": IntegrityStatus.VERIFIED.value,
        }))
        verified.validate()
        self._manifests[verified.backup_id] = verified
        return verified

    def verify_backup(self, backup_id: str, *, components: Mapping[str, Any],
                      source_version: str, environment: str) -> IntegrityStatus:
        manifest = self._manifests.get(backup_id)
        if manifest is None:
            return IntegrityStatus.UNKNOWN
        if manifest.source_version != source_version or \
                manifest.environment != environment:
            return IntegrityStatus.CORRUPTED
        for name, value in components.items():
            if name not in manifest.component_hashes:
                return IntegrityStatus.CORRUPTED
            if canonical_hash(value) != manifest.component_hashes[name]:
                return IntegrityStatus.CORRUPTED
        return IntegrityStatus.VERIFIED

    def authorize_restore(self, backup_id: str, *, components: Mapping[str, Any],
                          source_version: str, environment: str) -> BackupManifest:
        status = self.verify_backup(backup_id, components=components,
                                    source_version=source_version,
                                    environment=environment)
        if status is not IntegrityStatus.VERIFIED:
            raise ProtectionError(
                f"restore blocked: backup integrity is {status.value} "
                "(corrupt/UNKNOWN backups are never restored)",
                location="restore.authorize", rule_id="SEC-RESTORE",
                details={"backup_id": backup_id, "integrity": status.value})
        return self._manifests[backup_id]


# --------------------------------------------------------------------- #
# Security incidents                                                     #
# --------------------------------------------------------------------- #
class SecurityIncidentService:
    """SECTION 36 lifecycle; evidence immutable at every state."""

    def __init__(self, machines: StateMachineRegistry | None = None) -> None:
        self._machines = machines or build_state_machine_registry()
        self._incidents: dict[str, SecurityIncident] = {}

    def open(self, *, severity: IncidentSeverity, title: str,
             environment: str, affected_resources: Sequence[str],
             detected_at: datetime, detected_by: str,
             evidence: Mapping[str, Any]) -> SecurityIncident:
        self._machines.apply(INCIDENT_MACHINE, "DETECTED", "OPEN",
                             reason=title, actor=detected_by)
        incident = SecurityIncident(
            security_incident_id=new_identifier("security_incident_id"),
            severity=severity, title=title, affected_environment=environment,
            affected_resources=tuple(affected_resources),
            detected_at=ensure_utc(detected_at, location="incident.at"),
            detected_by=detected_by, status=SecurityIncidentStatus.OPEN,
            evidence=dict(evidence),
            timeline=({"state": "OPEN",
                       "at": ensure_utc(detected_at).isoformat(),
                       "by": detected_by},))
        incident.validate()
        self._incidents[incident.security_incident_id] = incident
        return incident

    def transition(self, incident_id: str, target: SecurityIncidentStatus,
                   *, actor: str, at: datetime, note: str,
                   human_approval: bool = False) -> SecurityIncident:
        current = self._incidents.get(incident_id)
        if current is None:
            raise ProtectionError("unknown incident",
                                  location="incident.transition",
                                  rule_id="SEC-INCIDENT")
        context = {"human_approval": human_approval}
        self._machines.apply(INCIDENT_MACHINE, current.status.value,
                             target.value, reason=note, actor=actor,
                             context=context,
                             timestamp=ensure_utc(at))
        closed = target is SecurityIncidentStatus.CLOSED
        updated = replace(
            current, status=target,
            timeline=tuple(current.timeline) + ({
                "state": target.value,
                "at": ensure_utc(at).isoformat(), "by": actor,
                "note": note},),
            closed_at=ensure_utc(at) if closed else current.closed_at,
            closure_approver=actor if closed else current.closure_approver)
        updated.validate()
        self._incidents[incident_id] = updated
        return updated

    def get(self, incident_id: str) -> SecurityIncident:
        record = self._incidents.get(incident_id)
        if record is None:
            raise ProtectionError("unknown incident",
                                  location="incident.get",
                                  rule_id="SEC-INCIDENT")
        return record


# --------------------------------------------------------------------- #
# Security events + observability                                        #
# --------------------------------------------------------------------- #
SECURITY_EVENT_TYPES: tuple[EventType, ...] = (
    EventType.AUTHENTICATION_SUCCEEDED, EventType.AUTHENTICATION_FAILED,
    EventType.SESSION_CREATED, EventType.SESSION_EXPIRED,
    EventType.SESSION_REVOKED, EventType.CREDENTIAL_ISSUED,
    EventType.CREDENTIAL_ROTATED, EventType.CREDENTIAL_REVOKED,
    EventType.PERMISSION_GRANTED, EventType.PERMISSION_REVOKED,
    EventType.AUTHORIZATION_ALLOWED, EventType.AUTHORIZATION_BLOCKED,
    EventType.APPROVAL_CREATED, EventType.APPROVAL_GRANTED,
    EventType.APPROVAL_REJECTED, EventType.SECURITY_POLICY_CHANGED,
    EventType.CONFIG_CHANGE_REQUESTED, EventType.CONFIG_CHANGE_APPROVED,
    EventType.REPLAY_DETECTED, EventType.RATE_LIMIT_TRIGGERED,
    EventType.PRIVILEGE_ESCALATION_BLOCKED,
    EventType.AUDIT_INTEGRITY_FAILURE, EventType.INCIDENT_OPENED,
    EventType.INCIDENT_CLOSED, EventType.BACKUP_CREATED,
    EventType.BACKUP_VERIFIED, EventType.RESTORE_VERIFIED,
)


def emit_security_event(event_type: EventType, *, payload: Mapping[str, Any],
                        environment: str, entity_id: str,
                        event_time: datetime | None = None) -> Event:
    moment = ensure_utc(event_time) if event_time else \
        datetime.now(timezone.utc)
    return build_event(
        event_type=event_type, source=SECURITY_SOURCE, source_id=entity_id,
        environment=environment,
        correlation_id=new_identifier("correlation_id"),
        event_time=moment, received_time=moment,
        payload=redact_payload(payload),
        event_id=new_identifier("event_id"), entity_id=entity_id)


SECRET_KEYS = ("password", "token", "api_key", "secret", "private_key",
               "credential", "session_secret")


def redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Security event payloads never carry secret values (SECTION 49)."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if any(marker in key.lower() for marker in SECRET_KEYS):
            cleaned[key] = "<redacted>"
        elif isinstance(value, Mapping):
            cleaned[key] = redact_payload(value)
        else:
            cleaned[key] = value
    return cleaned


class SecurityMetrics:
    """Observability counters only - never a second authority (SECTION 50)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def increment(self, name: str) -> None:
        self._counters[name] = self._counters.get(name, 0) + 1

    def value(self, name: str) -> int:
        return self._counters.get(name, 0)

    def snapshot(self) -> Mapping[str, int]:
        return dict(self._counters)
