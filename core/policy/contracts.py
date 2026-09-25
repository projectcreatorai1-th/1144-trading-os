"""Policy contract (owned by core.policy).

Phase 0 defines the contract only; policy calculation/evaluation arrives in
Phase 3. ACTIVE policies require an approver (fail closed), and status
changes go through the policy_status state machine.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment, Environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    TransitionRecord,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_not_before, ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.1.0"
POLICY_STATUS_MACHINE = "policy_status"


class PolicyType(Enum):
    ENTRY = "ENTRY"
    DIRECTION = "DIRECTION"
    GRID = "GRID"
    POSITION_SIZING = "POSITION_SIZING"
    EXPOSURE = "EXPOSURE"
    FREQUENCY = "FREQUENCY"
    RECOVERY = "RECOVERY"
    PROFIT = "PROFIT"
    NEWS = "NEWS"
    VOLATILITY = "VOLATILITY"
    SESSION = "SESSION"
    PORTFOLIO = "PORTFOLIO"
    EXECUTION = "EXECUTION"
    # Phase 3 risk policy types (policy contract 1.1.0)
    ACCOUNT_RISK_POLICY = "ACCOUNT_RISK_POLICY"
    POSITION_RISK_POLICY = "POSITION_RISK_POLICY"
    EXPOSURE_POLICY = "EXPOSURE_POLICY"
    DRAWDOWN_POLICY = "DRAWDOWN_POLICY"
    MARGIN_POLICY = "MARGIN_POLICY"
    VOLATILITY_POLICY = "VOLATILITY_POLICY"
    SPREAD_POLICY = "SPREAD_POLICY"
    LIQUIDITY_POLICY = "LIQUIDITY_POLICY"
    CORRELATION_POLICY = "CORRELATION_POLICY"
    NEWS_RISK_POLICY = "NEWS_RISK_POLICY"
    EXECUTION_PERMISSION_POLICY = "EXECUTION_PERMISSION_POLICY"
    GLOBAL_SAFETY_POLICY = "GLOBAL_SAFETY_POLICY"


class PolicyStatus(Enum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class Policy:
    policy_id: str
    policy_version: str
    policy_type: PolicyType
    status: PolicyStatus
    scope: str
    conditions: tuple[Mapping[str, Any], ...]
    actions: tuple[Mapping[str, Any], ...]
    limits: Mapping[str, Any]
    priority: int
    effective_from: datetime
    created_by: str
    created_at: datetime
    updated_at: datetime
    effective_to: datetime | None = None
    approved_by: str | None = None
    name: str | None = None
    description: str | None = None
    environment: str | None = None
    approved_at: datetime | None = None
    provenance: Any | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("policy_id", self.policy_id, location="policy.policy_id")
        SemVer.parse(self.policy_version, location="policy.policy_version")
        if not isinstance(self.policy_type, PolicyType):
            raise ContractValidationError(
                f"policy.policy_type must be a PolicyType, got {self.policy_type!r}",
                location="policy.policy_type",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.status, PolicyStatus):
            raise ContractValidationError(
                f"policy.status must be a PolicyStatus, got {self.status!r}",
                location="policy.status",
                rule_id="SCHEMA-ENUM",
            )
        for name in ("scope",):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ContractValidationError(
                    f"policy.{name} must be a non-empty string",
                    location=f"policy.{name}",
                )
        if not self.conditions:
            raise ContractValidationError(
                "policy.conditions must be non-empty (a policy without conditions is invalid)",
                location="policy.conditions",
            )
        for item in self.conditions:
            if not isinstance(item, Mapping):
                raise ContractValidationError(
                    "policy.conditions entries must be mappings",
                    location="policy.conditions",
                )
        if not self.actions:
            raise ContractValidationError(
                "policy.actions must be non-empty",
                location="policy.actions",
            )
        for item in self.actions:
            if not isinstance(item, Mapping):
                raise ContractValidationError(
                    "policy.actions entries must be mappings",
                    location="policy.actions",
                )
        if not isinstance(self.limits, Mapping) or not self.limits:
            raise ContractValidationError(
                "policy.limits must be a non-empty mapping",
                location="policy.limits",
            )
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or self.priority < 0:
            raise ContractValidationError(
                "policy.priority must be a non-negative integer",
                location="policy.priority",
            )
        effective_from = ensure_utc(self.effective_from, location="policy.effective_from")
        if self.effective_to is not None:
            ensure_not_before(
                self.effective_to,
                not_before=effective_from,
                location="policy.effective_to",
            )
        validate_identifier("user_id", self.created_by, location="policy.created_by")
        created_at = ensure_utc(self.created_at, location="policy.created_at")
        ensure_not_before(self.updated_at, not_before=created_at, location="policy.updated_at")
        if self.status is PolicyStatus.ACTIVE and not self.approved_by:
            raise ContractValidationError(
                "An ACTIVE policy requires approved_by (fail closed)",
                location="policy.approved_by",
                rule_id="POLICY-001",
            )
        if self.approved_by is not None:
            validate_identifier("user_id", self.approved_by, location="policy.approved_by")
        SemVer.parse(self.contract_version, location="policy.contract_version")

    def transition_status(
        self,
        target: PolicyStatus,
        *,
        reason: str,
        actor: str,
        approved: bool = False,
        machines: StateMachineRegistry | None = None,
    ) -> tuple["Policy", TransitionRecord]:
        """Apply a policy-status transition; ACTIVATION requires approval."""
        registry = machines or build_state_machine_registry()
        record = registry.apply(
            POLICY_STATUS_MACHINE,
            self.status.value,
            target.value,
            reason=reason,
            actor=actor,
            context={"policy_approved": approved},
        )
        new_status = PolicyStatus(target.value)
        approved_by = self.approved_by if target is not PolicyStatus.ACTIVE else self.approved_by
        updated = replace(self, status=new_status, approved_by=approved_by)
        try:
            updated.validate()
        except ContractValidationError as exc:
            raise ContractValidationError(
                f"Transition produces an invalid policy: {exc.message}",
                location="policy.transition",
                rule_id="POLICY-001",
                details={"target": target.value},
            ) from exc
        return updated, record

    def applies_to_environment(self, environment: Environment | str) -> bool:
        """Policies carry scope; environment scoping is explicit (Phase 3)."""
        parse_environment(environment, location="policy.environment_check")
        if self.environment is None:
            return True
        return self.environment == environment

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_type": self.policy_type.value,
            "status": self.status.value,
            "scope": self.scope,
            "conditions": [dict(c) for c in self.conditions],
            "actions": [dict(a) for a in self.actions],
            "limits": dict(self.limits),
            "priority": self.priority,
            "effective_from": ensure_utc(self.effective_from).isoformat(),
            "effective_to": ensure_utc(self.effective_to).isoformat() if self.effective_to else None,
            "created_by": self.created_by,
            "approved_by": self.approved_by,
            "created_at": ensure_utc(self.created_at).isoformat(),
            "updated_at": ensure_utc(self.updated_at).isoformat(),
            "name": self.name,
            "description": self.description,
            "environment": self.environment,
            "approved_at": ensure_utc(self.approved_at).isoformat() if self.approved_at else None,
        }

    @classmethod
    def from_storage(cls, data: Mapping[str, Any]) -> "Policy":
        from architecture.contracts.time import parse_canonical

        policy = cls(
            policy_id=data["policy_id"],
            policy_version=data["policy_version"],
            policy_type=PolicyType(data["policy_type"]),
            status=PolicyStatus(data["status"]),
            scope=data["scope"],
            conditions=tuple(dict(c) for c in data["conditions"]),
            actions=tuple(dict(a) for a in data["actions"]),
            limits=dict(data["limits"]),
            priority=int(data["priority"]),
            effective_from=parse_canonical(data["effective_from"]),
            effective_to=parse_canonical(data["effective_to"]) if data.get("effective_to") else None,
            created_by=data["created_by"],
            approved_by=data.get("approved_by"),
            created_at=parse_canonical(data["created_at"]),
            updated_at=parse_canonical(data["updated_at"]),
            name=data.get("name"),
            description=data.get("description"),
            environment=data.get("environment"),
            approved_at=parse_canonical(data["approved_at"]) if data.get("approved_at") else None,
        )
        policy.validate()
        return policy
