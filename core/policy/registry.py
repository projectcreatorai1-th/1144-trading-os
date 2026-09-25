"""Policy registry (owned by core.policy).

Lifecycle with strict approval separation (no self-approval), versioned
documents, deterministic resolution with EXPLICIT precedence (policy
priority), effective-time lookup and historical lookup. Unresolvable or
ambiguous resolution fails closed (SECTION 5/39)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from architecture.contracts.errors import ContractError, PermissionError_
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.policy.contracts import Policy, PolicyStatus, PolicyType
from core.policy.store import PolicyStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.security.contracts import Permission, Role, build_role_permission_registry

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class ActorContext:
    user_id: str
    role: Role

    def require(self, permission: Permission) -> None:
        registry = build_role_permission_registry()
        if not registry.has_permission(self.role, permission):
            raise PermissionError_(
                f"Role '{self.role.value}' lacks permission '{permission.value}'",
                location="policy.actor",
                details={"user_id": self.user_id, "role": self.role.value,
                         "permission": permission.value},
            )


class PolicyRegistryError(ContractError):
    rule_id = "POLICY-002"


class PolicyRegistry:
    def __init__(self, store: PolicyStore, audit: AuditRepository) -> None:
        self._store = store
        self._audit = audit

    # ------------------------------------------------------------------ #
    # Lifecycle (privileged, audited, least privilege)                    #
    # ------------------------------------------------------------------ #
    def create(self, policy: Policy, actor: ActorContext, at: datetime) -> Policy:
        actor.require(Permission.MODIFY_POLICY)
        if policy.status is not PolicyStatus.DRAFT:
            raise PolicyRegistryError(
                "New policies must start in DRAFT",
                location="policy.registry.create",
            )
        policy.validate()
        self._store.save(policy)
        self._audit_policy(actor, "POLICY_CREATED", policy, at=at,
                           before=None, after=policy.to_dict(), reason="policy draft created")
        return policy

    def submit_review(self, policy_id: str, policy_version: str, actor: ActorContext,
                      at: datetime) -> Policy:
        actor.require(Permission.MODIFY_POLICY)
        policy = self._store.get_version(policy_id, policy_version)
        updated, _ = policy.transition_status(
            PolicyStatus.REVIEW, reason="submitted for review", actor=actor.user_id
        )
        self._replace(updated)
        self._audit_policy(actor, "POLICY_REVIEW_SUBMITTED", updated, at=at,
                           before={"status": policy.status.value},
                           after={"status": "REVIEW"}, reason="review requested")
        return updated

    def approve(self, policy_id: str, policy_version: str, actor: ActorContext,
                at: datetime) -> Policy:
        actor.require(Permission.APPROVE)
        policy = self._store.get_version(policy_id, policy_version)
        if policy.created_by == actor.user_id:
            raise PolicyRegistryError(
                "Self-approval is forbidden: the approver must differ from the policy author",
                location="policy.registry.approve",
                rule_id="POLICY-003",
                details={"author": policy.created_by, "approver": actor.user_id},
            )
        updated, _ = policy.transition_status(
            PolicyStatus.APPROVED,
            reason=f"approved by {actor.user_id}",
            actor=actor.user_id,
            approved=True,
        )
        from dataclasses import replace

        updated = replace(updated, approved_by=actor.user_id,
                          approved_at=ensure_utc(at, location="policy.approved_at"))
        updated.validate()
        self._replace(updated)
        self._audit_policy(actor, "POLICY_APPROVED", updated, at=at,
                           before={"status": policy.status.value},
                           after={"status": "APPROVED", "approver": actor.user_id},
                           reason="policy approved")
        return updated

    def activate(self, policy_id: str, policy_version: str, actor: ActorContext, at: datetime) -> Policy:
        actor.require(Permission.MODIFY_POLICY)
        policy = self._store.get_version(policy_id, policy_version)
        if policy.status is not PolicyStatus.APPROVED:
            raise PolicyRegistryError(
                "The registry activates only APPROVED policies "
                "(DRAFT -> REVIEW -> APPROVED -> ACTIVE; the machine-level DRAFT->ACTIVE "
                "edge is reserved for emergency operator use, never programmatic)",
                location="policy.registry.activate",
                rule_id="POLICY-002",
                details={"current_status": policy.status.value},
            )
        updated, _ = policy.transition_status(
            PolicyStatus.ACTIVE, reason=f"activated by {actor.user_id}",
            actor=actor.user_id, approved=True,
        )
        self._replace(updated)
        self._audit_policy(actor, "POLICY_ACTIVATED", updated, at=at,
                           before={"status": "APPROVED"}, after={"status": "ACTIVE"},
                           reason="policy activated")
        return updated

    def suspend(self, policy_id: str, policy_version: str, actor: ActorContext,
                reason: str, at: datetime) -> Policy:
        actor.require(Permission.PAUSE)
        policy = self._store.get_version(policy_id, policy_version)
        updated, _ = policy.transition_status(
            PolicyStatus.SUSPENDED, reason=reason, actor=actor.user_id
        )
        self._replace(updated)
        self._audit_policy(actor, "POLICY_SUSPENDED", updated, at=at,
                           before={"status": policy.status.value}, after={"status": "SUSPENDED"},
                           reason=reason)
        return updated

    def retire(self, policy_id: str, policy_version: str, actor: ActorContext,
               reason: str, at: datetime) -> Policy:
        actor.require(Permission.MODIFY_POLICY)
        policy = self._store.get_version(policy_id, policy_version)
        updated, _ = policy.transition_status(
            PolicyStatus.RETIRED, reason=reason, actor=actor.user_id
        )
        self._replace(updated)
        self._audit_policy(actor, "POLICY_RETIRED", updated, at=at,
                           before={"status": policy.status.value}, after={"status": "RETIRED"},
                           reason=reason)
        return updated

    # ------------------------------------------------------------------ #
    # Resolution (deterministic, explicit precedence, fail closed)        #
    # ------------------------------------------------------------------ #
    def resolve_active(
        self,
        policy_type: PolicyType,
        *,
        environment: str,
        at: datetime,
        scope: str | None = None,
    ) -> Policy | None:
        moment = ensure_utc(at, location="policy.resolve.at")
        candidates = [
            policy for policy in self._store.iter_all_latest()
            if policy.policy_type is policy_type
            and policy.status is PolicyStatus.ACTIVE
            and policy.applies_to_environment(environment)
            and self._effective_at(policy, moment)
            and (scope is None or policy.scope in (scope, "*"))
        ]
        if not candidates:
            return None  # caller decides: missing policy fails closed
        priorities = {policy.priority for policy in candidates}
        if len(candidates) > 1 and len(priorities) == 1:
            raise PolicyRegistryError(
                f"Ambiguous policy resolution for {policy_type.value}: multiple ACTIVE "
                f"policies share priority {priorities.pop()} (explicit precedence required)",
                location="policy.registry.resolve",
                rule_id="POLICY-002",
                details={"candidates": [p.policy_id for p in candidates]},
            )
        return min(candidates, key=lambda policy: policy.priority)

    def version_at_time(self, policy_id: str, at: datetime) -> Policy | None:
        """Historical lookup: the version effective at the given time."""
        moment = ensure_utc(at, location="policy.history.at")
        effective = [
            policy for policy in self._store.iter_versions(policy_id)
            if self._effective_at(policy, moment)
        ]
        if not effective:
            return None
        from architecture.contracts.versioning import SemVer

        return max(effective, key=lambda policy: SemVer.parse(policy.policy_version))

    def get_version(self, policy_id: str, policy_version: str) -> Policy:
        return self._store.get_version(policy_id, policy_version)

    @staticmethod
    def _effective_at(policy: Policy, moment: datetime) -> bool:
        if moment < policy.effective_from:
            return False
        if policy.effective_to is not None and moment > policy.effective_to:
            return False
        return True

    def _replace(self, updated: Policy) -> None:
        """Policy versions are immutable documents; lifecycle moves write a NEW
        version (patch bump) so history is never overwritten."""
        from dataclasses import replace
        from architecture.contracts.versioning import SemVer

        current = SemVer.parse(updated.policy_version)
        patched = replace(updated, policy_version=f"{current.major}.{current.minor}.{current.patch + 1}")
        patched.validate()
        self._store.save(patched)

    def _audit_policy(self, actor: ActorContext, action: str, policy: Policy, *,
                      at: datetime, before: dict | None, after: dict, reason: str) -> None:
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER,
            actor_id=actor.user_id,
            action=action,
            entity_type="policy",
            entity_id=f"{policy.policy_id}@{policy.policy_version}",
            event_time=ensure_utc(at, location='policy.audit.at'),
            before=before,
            after=after,
            reason=reason,
            source="core.policy.registry",
            environment=policy.environment or "SIMULATION",
            correlation_id=policy.policy_id,
            policy_version=policy.policy_version,
        ))
