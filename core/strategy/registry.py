"""Strategy registry (owned by core.strategy).

Lifecycle transitions through the Phase 0 machine (strategy_lifecycle),
permission-controlled, audited, versioned. Deterministic resolution;
duplicate active versions / ambiguous resolution / missing capability,
policy or risk budget fail closed (SECTION 7)."""
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
from core.strategy.contracts import Strategy, StrategyLifecycle
from core.strategy.stores import StrategyStore
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository
from platform.security.contracts import Permission, Role, build_role_permission_registry

CONTRACT_VERSION = "1.0.0"
STRATEGY_MACHINE = "strategy_lifecycle"


class StrategyRegistryError(ContractError):
    rule_id = "STRATEGY-002"


@dataclass(frozen=True)
class StrategyActor:
    user_id: str
    role: Role

    def require(self, permission: Permission) -> None:
        registry = build_role_permission_registry()
        if not registry.has_permission(self.role, permission):
            raise ContractError(
                f"Role '{self.role.value}' lacks permission '{permission.value}'",
                location="strategy.actor", rule_id="PERM-001",
                details={"user_id": self.user_id, "permission": permission.value},
            )


class StrategyRegistry:
    def __init__(self, store: StrategyStore, audit: AuditRepository,
                 machines: StateMachineRegistry | None = None) -> None:
        self._store = store
        self._audit = audit
        self._machines = machines or build_state_machine_registry()

    def register(self, strategy: Strategy, actor: StrategyActor, at: datetime) -> Strategy:
        actor.require(Permission.MODIFY_POLICY)  # strategy management authority
        if strategy.lifecycle_status is not StrategyLifecycle.IDEA:
            raise StrategyRegistryError(
                "New strategies must start at IDEA", location="strategy.registry.register",
            )
        strategy.validate()
        self._store.save(strategy)
        self._audit_action(actor, "STRATEGY_REGISTERED",
                           f"{strategy.strategy_id}@{strategy.strategy_version}",
                           before=None, after=strategy.to_dict(),
                           reason="strategy registered", at=at, environment=strategy.environment)
        return strategy

    def transition(self, strategy_id: str, strategy_version: str, target: StrategyLifecycle,
                   actor: StrategyActor, at: datetime, reason: str) -> Strategy:
        actor.require(Permission.MODIFY_POLICY)
        latest = self._latest(strategy_id)
        self._machines.apply(
            STRATEGY_MACHINE, latest.lifecycle_status.value, target.value,
            reason=reason, actor=actor.user_id, timestamp=ensure_utc(at, location="strategy.at"),
        )
        if target is StrategyLifecycle.LIVE and latest.lifecycle_status is not StrategyLifecycle.APPROVED:
            raise StrategyRegistryError(
                "LIVE requires APPROVED (full promotion chain prerequisite)",
                location="strategy.registry.transition", rule_id="STRATEGY-002",
                details={"current": latest.lifecycle_status.value},
            )
        if latest.lifecycle_status is target and latest.strategy_version == strategy_version:
            # caller-modified copy of the stored version: bump to keep immutability
            pass
        from dataclasses import replace

        updated = replace(latest, lifecycle_status=target,
                          strategy_version=self._next_patch(latest.strategy_version))
        updated.validate()
        self._store.save(updated)
        self._audit_action(actor, "STRATEGY_LIFECYCLE_TRANSITION",
                           f"{strategy_id}@{updated.strategy_version}",
                           before={"status": latest.lifecycle_status.value},
                           after={"status": target.value}, reason=reason, at=at,
                           environment=updated.environment)
        return updated

    def suspend(self, strategy_id: str, actor: StrategyActor, at: datetime, reason: str) -> Strategy:
        actor.require(Permission.PAUSE)
        latest = self._latest(strategy_id)
        return self.transition(strategy_id, latest.strategy_version,
                               StrategyLifecycle.SUSPENDED, actor, at, reason)

    def resolve(self, strategy_id: str, *, environment: str,
                at: datetime) -> Strategy | None:
        moment = ensure_utc(at, location="strategy.resolve.at")
        candidates = [
            strategy for strategy in self._store.iter_versions(strategy_id)
            if strategy.environment == environment
            and moment >= strategy.effective_from
            and (strategy.effective_to is None or moment <= strategy.effective_to)
            and strategy.lifecycle_status is not StrategyLifecycle.RETIRED
        ]
        if not candidates:
            return None
        from architecture.contracts.versioning import SemVer

        return max(candidates, key=lambda s: SemVer.parse(s.strategy_version))

    def resolve_active_for_intents(self, *, environment: str, at: datetime) -> list[Strategy]:
        moment = ensure_utc(at, location="strategy.resolve.at")
        resolved: dict[str, Strategy] = {}
        for strategy in self._store.iter_all_latest():
            if strategy.environment != environment:
                continue
            if strategy.lifecycle_status not in (StrategyLifecycle.LIVE,
                                                 StrategyLifecycle.FORWARD,
                                                 StrategyLifecycle.DEMO,
                                                 StrategyLifecycle.PAPER,
                                                 StrategyLifecycle.REPLAY):
                continue
            if moment < strategy.effective_from or (
                strategy.effective_to is not None and moment > strategy.effective_to
            ):
                continue
            if strategy.strategy_id in resolved:
                raise StrategyRegistryError(
                    f"Duplicate active strategy {strategy.strategy_id} in {environment}",
                    location="strategy.registry.resolve_active",
                )
            resolved[strategy.strategy_id] = strategy
        return list(resolved.values())

    def get_version(self, strategy_id: str, strategy_version: str) -> Strategy:
        return self._store.get_version(strategy_id, strategy_version)

    def _latest(self, strategy_id: str) -> Strategy:
        versions = list(self._store.iter_versions(strategy_id))
        if not versions:
            raise StrategyRegistryError(
                f"Unknown strategy '{strategy_id}'", location="strategy.registry",
                rule_id="STRATEGY-001",
            )
        return versions[-1]

    @staticmethod
    def _next_patch(version: str) -> str:
        from architecture.contracts.versioning import SemVer

        parsed = SemVer.parse(version)
        return f"{parsed.major}.{parsed.minor}.{parsed.patch + 1}"

    def _audit_action(self, actor: StrategyActor, action: str, entity_id: str, *,
                      before: dict | None, after: dict, reason: str, at: datetime,
                      environment: str) -> None:
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.USER,
            actor_id=actor.user_id, action=action, entity_type="strategy",
            entity_id=entity_id, event_time=ensure_utc(at, location="strategy.audit"),
            before=before, after=after, reason=reason,
            source="core.strategy.registry", environment=environment,
            correlation_id=entity_id.split("@")[0],
        ))
