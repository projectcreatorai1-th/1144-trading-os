"""Permission contract tests (SECTION 17) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import ContractError, PermissionError_
from platform.security.contracts import (
    Permission,
    Role,
    build_role_permission_registry,
)
from tests.factories import at, make_permission_grant

REGISTRY = build_role_permission_registry()

ALL_PERMISSIONS = {
    "VIEW", "ANALYZE", "RESEARCH", "SIMULATE", "PAPER_TRADE", "DEMO_TRADE",
    "LIVE_TRADE", "MODIFY_POLICY", "MODIFY_RISK", "APPROVE", "PAUSE",
    "CLOSE_ONLY", "EMERGENCY_STOP", "ADMIN",
    # Phase 8 security/governance permissions (permissions.yaml 1.1.0)
    "CONFIGURE", "MANAGE_CREDENTIALS", "MANAGE_SECURITY", "MANAGE_GOVERNANCE",
}

ALL_ROLES = {
    "VIEWER", "ANALYST", "RESEARCHER", "TRADER", "RISK_MANAGER",
    "OPERATOR", "APPROVER", "ADMIN",
    # Phase 8 (permissions.yaml 1.1.0)
    "SECURITY_ADMIN",
}


class TestPermissionRegistry:
    def test_all_permissions_registered(self):
        assert {p.value for p in Permission} == ALL_PERMISSIONS

    def test_all_roles_registered(self):
        assert {r.value for r in Role} == ALL_ROLES

    def test_every_role_has_view(self):
        for role in REGISTRY.roles:
            assert REGISTRY.has_permission(role, "VIEW")

    def test_live_trade_restricted_to_admin_by_default(self):
        for role in REGISTRY.roles:
            assert REGISTRY.has_permission(role, "LIVE_TRADE") is (role == "ADMIN")

    def test_has_permission_false_for_unheld(self):
        assert REGISTRY.has_permission("VIEWER", "MODIFY_POLICY") is False

    def test_unknown_role_rejected(self):
        with pytest.raises(ContractError):
            REGISTRY.permissions_for("WIZARD")


class TestPermissionGrants:
    def test_valid_grant(self):
        make_permission_grant().validate()

    def test_viewer_grant(self):
        from tests.factories import make_permission_grant as grant

        grant(role=Role.VIEWER, permission=Permission.VIEW, environment="PAPER").validate()

    def test_environment_scoped_simulation_grant(self):
        from tests.factories import make_permission_grant as grant

        grant(
            role=Role.RESEARCHER, permission=Permission.SIMULATE, environment="SIMULATION"
        ).validate()


class TestPermissionFailures:
    def test_unheld_permission_rejected(self):
        with pytest.raises(PermissionError_):
            make_permission_grant(
                role=Role.TRADER, permission=Permission.EMERGENCY_STOP
            ).validate()

    def test_live_trade_in_wrong_environment_rejected(self):
        with pytest.raises(PermissionError_) as excinfo:
            make_permission_grant(
                role=Role.ADMIN, permission=Permission.LIVE_TRADE, environment="PAPER"
            ).validate()
        assert excinfo.value.rule_id == "ENV-001"

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractError):
            make_permission_grant(environment="SHADOW").validate()

    def test_unknown_grantor_rejected(self):
        with pytest.raises(ContractError):
            make_permission_grant(granted_by="root").validate()

    def test_grant_is_immutable(self):
        grant_obj = make_permission_grant()
        with pytest.raises(FrozenInstanceError):
            grant_obj.role = Role.VIEWER
