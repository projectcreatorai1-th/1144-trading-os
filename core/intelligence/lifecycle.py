"""Model lifecycle (owned by core.intelligence).

Reuses the Phase 0 state machine (model_lifecycle) - the intelligence
plane never builds its own state machine. Promotion steps into RESEARCH_APPROVED/SHADOW/PAPER/DEMO/
PRODUCTION_ELIGIBLE require HUMAN_APPROVAL: the model itself can never
self-promote, self-deploy or enable LIVE (SECTION 31/41/84).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import ModelDefinition
from core.intelligence.registry import ModelRegistry
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.security.contracts import Permission, Role, \
    build_role_permission_registry

CONTRACT_VERSION = "1.0.0"
MODEL_MACHINE = "model_lifecycle"

#: Transitions that require an operator/approver permission in addition to
#: the machine's HUMAN_APPROVAL requirement.
PROMOTION_PERMISSIONS = {
    "RESEARCH_APPROVED": Permission.APPROVE,
    "SHADOW": Permission.APPROVE,
    "PAPER": Permission.APPROVE,
    "DEMO": Permission.APPROVE,
    "PRODUCTION_ELIGIBLE": Permission.APPROVE,
}


class ModelLifecycleError(ContractError):
    rule_id = "MLIFECYCLE"


@dataclass(frozen=True)
class ModelActor:
    user_id: str
    role: Role

    def require(self, permission: Permission) -> None:
        registry = build_role_permission_registry()
        if not registry.has_permission(self.role, permission):
            raise ContractError(
                f"Role '{self.role.value}' lacks permission "
                f"'{permission.value}'",
                location="model.actor", rule_id="PERM-001",
                details={"user_id": self.user_id,
                         "permission": permission.value})


class ModelLifecycleService:
    """Permission-controlled, audited model lifecycle transitions."""

    def __init__(self, registry: ModelRegistry, audit: AuditRepository,
                 machines: StateMachineRegistry | None = None) -> None:
        self._registry = registry
        self._audit = audit
        self._machines = machines or build_state_machine_registry()

    def transition(self, model_id: str, model_version: str, target: str,
                   actor: ModelActor, at: datetime, reason: str,
                   human_approval: bool = False) -> ModelDefinition:
        model = self._registry.get(model_id, model_version)
        context = {"human_approval": human_approval}
        self._machines.apply(
            MODEL_MACHINE, model.status, target,
            reason=reason, actor=actor.user_id, context=context,
            timestamp=ensure_utc(at, location="model.at"),
        )
        permission = PROMOTION_PERMISSIONS.get(target)
        if permission is not None:
            if not human_approval:
                raise ModelLifecycleError(
                    f"transition to {target} requires HUMAN_APPROVAL - a "
                    "model can never promote itself",
                    location="model.transition", rule_id="AI-PROMOTION",
                    details={"target": target})
            actor.require(permission)
        from dataclasses import replace
        # status is part of the content identity: every transition creates
        # a new immutable model version (append-only, never in-place).
        new_version = self._next_patch(model.model_version)
        updated = replace(model, status=target, model_version=new_version)
        object.__setattr__(updated, "model_hash", updated.compute_model_hash())
        updated.validate()
        self._registry.register(updated)
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER,
            actor_id=actor.user_id,
            action="MODEL_LIFECYCLE_TRANSITION",
            entity_type="model",
            entity_id=f"{model_id}@{new_version}",
            event_time=ensure_utc(at, location="model.audit"),
            before={"status": model.status},
            after={"status": target},
            reason=reason,
            source="core.intelligence.lifecycle",
            environment=model.training_environment,
            correlation_id=model_id,
            model_version=new_version,
        ))
        return updated

    def _current(self, model_id: str) -> ModelDefinition:
        versions = [m for m in self._registry if m.model_id == model_id]
        if not versions:
            raise ModelLifecycleError(
                f"unknown model '{model_id}'",
                location="model.current", rule_id="MLIFECYCLE-001")
        return max(versions, key=lambda m: tuple(
            int(p) for p in m.model_version.split(".")))

    @staticmethod
    def _next_patch(version: str) -> str:
        major, minor, patch = (int(p) for p in version.split("."))
        return f"{major}.{minor}.{patch + 1}"

    def suspend(self, model_id: str, model_version: str, actor: ModelActor,
                at: datetime, reason: str) -> ModelDefinition:
        """Existing policy/state mechanisms may suspend a model (SECTION 125).
        Historical inference remains queryable."""
        actor.require(Permission.PAUSE)
        return self.transition(model_id, model_version, "SUSPENDED",
                               actor, at, reason)

    def retire(self, model_id: str, model_version: str, actor: ModelActor,
               at: datetime, reason: str) -> ModelDefinition:
        """Retirement keeps the model immutable and queryable (SECTION 88)."""
        actor.require(Permission.PAUSE)
        return self.transition(model_id, model_version, "RETIRED",
                               actor, at, reason)
