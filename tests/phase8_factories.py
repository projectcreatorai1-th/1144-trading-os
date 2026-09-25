"""Phase 8 test factories: deterministic security environment (SYNTHETIC)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.security.authentication import AuthenticationService, SessionService
from core.security.authorization import AuthorizationService
from core.security.contracts import AuthMethod
from core.security.protection import AuditChain
from core.security.services import MakerCheckerService
from platform.security.contracts import Permission, Role

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
H64 = "a" * 64

ACTOR_MAKER = "usr_" + "1" * 32
ACTOR_CHECKER = "usr_" + "2" * 32
ACTOR_TRADER = "usr_" + "3" * 32


def security_env():
    """A complete, wired-up security stack (SYNTHETIC credentials only)."""
    auth = AuthenticationService()
    sessions = SessionService()
    authz = AuthorizationService(sessions)
    approvals = MakerCheckerService()
    chain = AuditChain()
    return {"auth": auth, "sessions": sessions, "authz": authz,
            "approvals": approvals, "chain": chain}


def issue_session(env, *, actor_id: str, environment: str, role: str,
                  permissions, secret: str, at=T0, method=AuthMethod.TOKEN,
                  ttl=None):
    credential, _ = env["auth"].issue(
        actor_id=actor_id, environment=environment, method=method,
        secret_material=secret, at=at, **({"ttl": ttl} if ttl else {}))
    result = env["auth"].authenticate(
        credential_id=credential.credential_id, secret_material=secret,
        environment=environment, at=at + timedelta(minutes=1))
    session = env["sessions"].create(
        authentication=result, role=role, permissions=tuple(permissions))
    return credential, session


def demo_trader_session(env):
    return issue_session(env, actor_id=ACTOR_TRADER, environment="DEMO",
                         role="TRADER",
                         permissions=("VIEW", "ANALYZE", "DEMO_TRADE"),
                         secret="synthetic-trader-secret")


def demo_approver_session(env, actor_id=ACTOR_MAKER):
    return issue_session(env, actor_id=actor_id, environment="DEMO",
                         role="APPROVER",
                         permissions=("VIEW", "ANALYZE", "MODIFY_POLICY",
                                      "APPROVE"),
                         secret="synthetic-approver-secret")


def live_admin_session(env):
    return issue_session(env, actor_id=ACTOR_MAKER, environment="LIVE",
                         role="ADMIN",
                         permissions=("VIEW", "ANALYZE", "LIVE_TRADE",
                                      "APPROVE", "MODIFY_RISK"),
                         secret="synthetic-admin-secret")


def active_approval(env, *, operation="POLICY_CHANGE",
                    resource="pol_" + "1" * 32, environment="DEMO",
                    maker=ACTOR_MAKER, checker=ACTOR_CHECKER, at=T0):
    from core.security.contracts import ApprovalStatus
    submission = env["approvals"].submit(
        operation=operation, resource=resource, environment=environment,
        maker_actor_id=maker, at=at)
    approved = env["approvals"].approve(
        submission.approval_id, checker_actor_id=checker,
        checker_kind="HUMAN", at=at + timedelta(minutes=1))
    assert approved.status is ApprovalStatus.ACTIVE
    return approved
