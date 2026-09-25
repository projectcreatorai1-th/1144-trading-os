"""Configuration governance (core.policy, Phase 15).

Configuration is a first-class governed object: every config version walks
CREATE -> VALIDATE -> REVIEW -> APPROVE -> ACTIVATE (audited), rollback is
explicit, and the historical question "which version was active at time T"
is answered from the versioned records - never from memory.

REUSE: the kernel config envelope validation and the existing audit
repository; this module adds the governance lifecycle on top (no second
configuration authority - activate() is what an APPLIED policy/config
references).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

CONFIG_STATES = ("DRAFT", "REVIEWED", "APPROVED", "ACTIVE", "ROLLED_BACK")
_TRANSITIONS = {"DRAFT": ("REVIEWED",),
                "REVIEWED": ("APPROVED",),
                "APPROVED": ("ACTIVE",),
                "ACTIVE": ("ROLLED_BACK",),
                "ROLLED_BACK": ()}


class ConfigStatus(Enum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class GovernedConfig:
    """§17.1 identity fields. `content` is an opaque validated mapping; the
    hash pins the exact bytes that were approved."""

    config_id: str
    version: str
    kind: str
    content: Mapping[str, Any]
    status: ConfigStatus
    created_at: datetime
    created_by: str
    approved_by: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    content_sha256: str = ""

    def validate(self) -> None:
        if not self.config_id or not self.version:
            raise ContractError("config_id and version required",
                                location="config.identity", rule_id="CFG-001")
        if not self.kind:
            raise ContractError("config.kind required",
                                location="config.kind", rule_id="CFG-001")
        if not isinstance(self.content, Mapping):
            raise ContractError("config.content must be a mapping",
                                location="config.content", rule_id="CFG-002")
        ensure_utc(self.created_at, location="config.created_at")
        if self.status not in (ConfigStatus.DRAFT, ConfigStatus.REVIEWED)                 and not self.approved_by:
            raise ContractError(
                "only DRAFT configs may lack an approver",
                location="config.approved_by", rule_id="CFG-003")

    def with_hash(self) -> "GovernedConfig":
        payload = json.dumps(self.content, sort_keys=True,
                             separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return replace(self, content_sha256=digest)


class ConfigGovernor:
    """Lifecycle authority for governed configs (audited, versioned)."""

    def __init__(self, audit: AuditRepository) -> None:
        if not isinstance(audit, AuditRepository):
            raise ContractError("ConfigGovernor requires an AuditRepository",
                                location="config.init", rule_id="CFG-004")
        self._audit = audit
        self._versions: dict[tuple[str, str], GovernedConfig] = {}

    def create(self, *, kind: str, content: Mapping[str, Any],
               created_by: str, version: str = "1.0.0",
               at: datetime | None = None) -> GovernedConfig:
        moment = ensure_utc(at) if at else utc_now()
        config = GovernedConfig(
            config_id=new_identifier("config_id"), version=version,
            kind=kind, content=dict(content), status=ConfigStatus.DRAFT,
            created_at=moment, created_by=created_by).with_hash()
        config.validate()
        self._audit_config(config, "CONFIG_CREATED", moment, actor=created_by)
        self._versions[(config.config_id, config.version)] = config
        return config

    def transition(self, config_id: str, version: str, target: ConfigStatus,
                   *, actor: str, at: datetime | None = None) -> GovernedConfig:
        key = (config_id, version)
        config = self._versions.get(key)
        if config is None:
            raise ContractError(f"unknown config {config_id}@{version}",
                                location="config.lookup", rule_id="CFG-005")
        if target.value not in _TRANSITIONS[config.status.value]:
            raise ContractError(
                f"illegal config transition {config.status.value} -> "
                f"{target.value}",
                location="config.transition", rule_id="CFG-006",
                details={"legal": _TRANSITIONS[config.status.value]})
        moment = ensure_utc(at) if at else utc_now()
        updated = replace(
            config, status=target,
            approved_by=actor if target is ConfigStatus.APPROVED
            else config.approved_by,
            effective_from=moment if target is ConfigStatus.ACTIVE
            else config.effective_from,
            effective_to=moment if target is ConfigStatus.ROLLED_BACK
            else config.effective_to)
        updated.validate()
        self._versions[key] = updated
        self._audit_config(updated, f"CONFIG_{target.value}", moment,
                           actor=actor)
        return updated

    def active_at(self, kind: str, moment: datetime) -> GovernedConfig | None:
        """§17.2 historical reconstruction: which version governed `kind`
        at time T (from effective windows, never memory)."""
        moment = ensure_utc(moment, location="config.active_at")
        candidates = [c for c in self._versions.values()
                      if c.kind == kind and c.status in (
                          ConfigStatus.ACTIVE, ConfigStatus.ROLLED_BACK)
                      and c.effective_from is not None
                      and c.effective_from <= moment
                      and (c.effective_to is None
                           or moment <= c.effective_to)]
        return max(candidates, key=lambda c: c.effective_from) \
            if candidates else None

    def get(self, config_id: str, version: str) -> GovernedConfig:
        config = self._versions.get((config_id, version))
        if config is None:
            raise ContractError(f"unknown config {config_id}@{version}",
                                location="config.lookup", rule_id="CFG-005")
        return config

    def _audit_config(self, config: GovernedConfig, action: str,
                      moment: datetime, *, actor: str) -> None:
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.USER,
            actor_id=actor, action=action, entity_type="governed_config",
            entity_id=f"{config.config_id}@{config.version}",
            event_time=ensure_utc(moment, location="config.audit"),
            before=None,
            after={"status": config.status.value,
                   "kind": config.kind,
                   "content_sha256": config.content_sha256},
            reason="configuration governance", source="core.policy.config",
            environment="SIMULATION", correlation_id=config.config_id))
