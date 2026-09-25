"""Permission contract (owned by platform.security).

The role-permission matrix lives in architecture/permissions.yaml (single
source of truth). GUI code must never hard-code role assumptions; it asks
this contract (RULE: permissions live in Core/Platform contracts).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractError, ContractValidationError, PermissionError_
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.registry import load_registry
from architecture.contracts.time import ensure_utc

CONTRACT_VERSION = "1.1.0"


class Permission(Enum):
    VIEW = "VIEW"
    ANALYZE = "ANALYZE"
    RESEARCH = "RESEARCH"
    SIMULATE = "SIMULATE"
    PAPER_TRADE = "PAPER_TRADE"
    DEMO_TRADE = "DEMO_TRADE"
    LIVE_TRADE = "LIVE_TRADE"
    MODIFY_POLICY = "MODIFY_POLICY"
    MODIFY_RISK = "MODIFY_RISK"
    APPROVE = "APPROVE"
    PAUSE = "PAUSE"
    CLOSE_ONLY = "CLOSE_ONLY"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    ADMIN = "ADMIN"
    # Phase 8 security/governance permissions (permissions.yaml 1.1.0)
    CONFIGURE = "CONFIGURE"
    MANAGE_CREDENTIALS = "MANAGE_CREDENTIALS"
    MANAGE_SECURITY = "MANAGE_SECURITY"
    MANAGE_GOVERNANCE = "MANAGE_GOVERNANCE"


class Role(Enum):
    VIEWER = "VIEWER"
    ANALYST = "ANALYST"
    RESEARCHER = "RESEARCHER"
    TRADER = "TRADER"
    RISK_MANAGER = "RISK_MANAGER"
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"
    SECURITY_ADMIN = "SECURITY_ADMIN"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class PermissionGrant:
    role: Role
    permission: Permission
    environment: str
    granted_by: str
    granted_at: datetime
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        if not isinstance(self.role, Role):
            raise ContractValidationError(
                f"grant.role must be a Role, got {self.role!r}",
                location="grant.role",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.permission, Permission):
            raise ContractValidationError(
                f"grant.permission must be a Permission, got {self.permission!r}",
                location="grant.permission",
                rule_id="SCHEMA-ENUM",
            )
        parse_environment(self.environment, location="grant.environment")
        validate_identifier("user_id", self.granted_by, location="grant.granted_by")
        ensure_utc(self.granted_at, location="grant.granted_at")
        registry = build_role_permission_registry()
        registry.check_grant(self.role, self.permission, self.environment)


class RolePermissionRegistry:
    """Reads and enforces architecture/permissions.yaml."""

    def __init__(
        self,
        matrix: Mapping[str, frozenset[str]],
        restrictions: tuple[Mapping[str, Any], ...],
    ) -> None:
        self._matrix = dict(matrix)
        self._restrictions = restrictions

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._matrix))

    def permissions_for(self, role: Role | str) -> frozenset[Permission]:
        key = role.value if isinstance(role, Role) else role
        if key not in self._matrix:
            raise ContractError(
                f"Unknown role '{key}'",
                location="permissions",
                rule_id="PERM-001",
                details={"known_roles": list(self.roles)},
            )
        return frozenset(Permission(p) for p in self._matrix[key])

    def has_permission(self, role: Role | str, permission: Permission | str) -> bool:
        target = permission.value if isinstance(permission, Permission) else permission
        try:
            return target in {p.value for p in self.permissions_for(role)}
        except ContractError:
            return False

    def allowed_environments(self, permission: Permission | str) -> list[str] | None:
        """None = unrestricted; otherwise the environments where the permission applies."""
        target = permission.value if isinstance(permission, Permission) else permission
        for restriction in self._restrictions:
            if restriction["permission"] == target:
                return list(restriction["environments"])
        return None

    def check_grant(
        self, role: Role | str, permission: Permission | str, environment: str
    ) -> None:
        parse_environment(environment, location="grant.environment")
        if not self.has_permission(role, permission):
            raise PermissionError_(
                f"Role '{role.value if isinstance(role, Role) else role}' does not hold "
                f"permission '{permission.value if isinstance(permission, Permission) else permission}'",
                location="permissions.grant",
                details={
                    "role": role.value if isinstance(role, Role) else str(role),
                    "permission": permission.value if isinstance(permission, Permission) else str(permission),
                },
            )
        allowed = self.allowed_environments(permission)
        if allowed is not None and environment not in allowed:
            raise PermissionError_(
                f"Permission '{permission.value if isinstance(permission, Permission) else permission}' "
                f"is restricted to environments {allowed}; grant targets {environment}",
                location="permissions.grant",
                rule_id="ENV-001",
            )


@lru_cache(maxsize=None)
def build_role_permission_registry() -> RolePermissionRegistry:
    data = load_registry("permissions.yaml")
    matrix: dict[str, frozenset[str]] = {}
    for entry in data["roles"]:
        role_name = entry["role"]
        if role_name in matrix:
            raise ContractError(
                f"Duplicate role '{role_name}' in permission registry",
                location="permissions",
                rule_id="ARCH-003",
            )
        declared = entry["permissions"]
        known = {p.value for p in Permission}
        unknown = [p for p in declared if p not in known]
        if unknown:
            raise ContractError(
                f"Role '{role_name}' declares unknown permissions: {unknown}",
                location="permissions",
                rule_id="ARCH-008",
            )
        matrix[role_name] = frozenset(declared)
    if set(matrix) != {r.value for r in Role}:
        raise ContractError(
            "Permission registry roles do not match the Role contract enum",
            location="permissions",
            rule_id="ARCH-008",
            details={"registry_roles": sorted(matrix), "contract_roles": sorted(r.value for r in Role)},
        )
    restrictions = tuple(data.get("environment_permission_restrictions", []))
    return RolePermissionRegistry(matrix, restrictions)
