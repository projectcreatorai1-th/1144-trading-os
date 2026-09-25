"""Maker-checker approvals (owned by core.security).

SECTION 12: maker submits, a DIFFERENT human checker approves. The
contract itself makes self-approval unrepresentable; the service also
refuses non-human checkers (AI can never check - SECTION 3.5/13).
Approval records are immutable; rejection leaves immutable evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.state_machine import (
    StateMachineRegistry,
    build_state_machine_registry,
)
from architecture.contracts.time import ensure_utc

from core.security.contracts import (
    NON_HUMAN_CHECKERS,
    ApprovalRecord,
    ApprovalStatus,
)

CONTRACT_VERSION = "1.0.0"
APPROVAL_MACHINE = "approval_flow"
APPROVAL_TTL = timedelta(hours=4)


class ApprovalError(ContractError):
    rule_id = "SEC-APPROVAL"


def is_human_checker(actor_id: str, actor_kind: str) -> bool:
    """AI and automated actors can never satisfy a human approval."""
    return actor_kind not in NON_HUMAN_CHECKERS


class MakerCheckerService:
    """Generic maker-checker governance flow."""

    def __init__(self, machines: StateMachineRegistry | None = None) -> None:
        self._machines = machines or build_state_machine_registry()
        self._records: dict[str, ApprovalRecord] = {}

    def submit(self, *, operation: str, resource: str, environment: str,
               maker_actor_id: str, at: datetime, reason: str = "",
               evidence: Mapping[str, Any] | None = None) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=new_identifier("approval_id"),
            operation=operation, resource=resource, environment=environment,
            maker_actor_id=maker_actor_id, checker_actor_id=None,
            status=ApprovalStatus.SUBMITTED,
            submitted_at=ensure_utc(at, location="approval.submit.at"),
            reason=reason, evidence=dict(evidence or {}),
            expires_at=ensure_utc(at) + APPROVAL_TTL)
        record.validate()
        self._records[record.approval_id] = record
        return record

    def approve(self, approval_id: str, *, checker_actor_id: str,
                checker_kind: str, at: datetime,
                note: str = "") -> ApprovalRecord:
        record = self._require(approval_id)
        moment = ensure_utc(at, location="approval.approve.at")
        if not is_human_checker(checker_actor_id, checker_kind):
            raise ApprovalError(
                f"checker kind '{checker_kind}' can never approve "
                "(AI cannot satisfy a human approval)",
                location="approval.approve", rule_id="SEC-AI-APPROVAL",
                details={"checker_kind": checker_kind})
        if checker_actor_id == record.maker_actor_id:
            raise ApprovalError(
                "self-approval blocked: the checker must differ from the "
                "maker",
                location="approval.approve", rule_id="SEC-SELF-APPROVAL")
        if record.status is not ApprovalStatus.SUBMITTED:
            raise ApprovalError(
                f"approval is {record.status.value}, not SUBMITTED",
                location="approval.approve", rule_id="SEC-APPROVAL")
        if record.expires_at is not None and \
                ensure_utc(record.expires_at) < moment:
            raise ApprovalError(
                "approval request expired (stale approvals never grant)",
                location="approval.approve", rule_id="SEC-STALE-APPROVAL")
        self._machines.apply(APPROVAL_MACHINE, record.status.value,
                             "REVIEW", reason="check started",
                             actor=checker_actor_id)
        self._machines.apply(APPROVAL_MACHINE, "REVIEW", "APPROVED",
                             reason=note or "approved", actor=checker_actor_id,
                             context={"human_approval": True})
        self._machines.apply(APPROVAL_MACHINE, "APPROVED", "ACTIVE",
                             reason="activation", actor=checker_actor_id)
        approved = replace(
            record, status=ApprovalStatus.ACTIVE,
            checker_actor_id=checker_actor_id, decided_at=moment,
            evidence={**record.evidence, "approval_note": note})
        approved.validate()
        self._records[approval_id] = approved
        return approved

    def reject(self, approval_id: str, *, checker_actor_id: str,
               at: datetime, reason: str) -> ApprovalRecord:
        record = self._require(approval_id)
        if checker_actor_id == record.maker_actor_id:
            raise ApprovalError(
                "self-rejection of own submission is not a valid check",
                location="approval.reject", rule_id="SEC-SELF-APPROVAL")
        self._machines.apply(APPROVAL_MACHINE, record.status.value,
                             "REVIEW", reason="check started",
                             actor=checker_actor_id)
        self._machines.apply(APPROVAL_MACHINE, "REVIEW", "REJECTED",
                             reason=reason, actor=checker_actor_id)
        rejected = replace(record, status=ApprovalStatus.REJECTED,
                           checker_actor_id=checker_actor_id,
                           decided_at=ensure_utc(at))
        rejected.validate()
        self._records[approval_id] = rejected
        return rejected

    def require_active(self, approval_id: str, *,
                       operation: str) -> ApprovalRecord:
        """The gate every privileged operation calls: only an ACTIVE
        approval created by a DIFFERENT human opens the path."""
        record = self._require(approval_id)
        if record.operation != operation:
            raise ApprovalError(
                "approval does not cover this operation",
                location="approval.require", rule_id="SEC-APPROVAL",
                details={"expected": operation,
                         "actual": record.operation})
        if record.status is not ApprovalStatus.ACTIVE:
            raise ApprovalError(
                f"approval is {record.status.value} (only ACTIVE grants)",
                location="approval.require", rule_id="SEC-APPROVAL")
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        return self._require(approval_id)

    def _require(self, approval_id: str) -> ApprovalRecord:
        record = self._records.get(approval_id)
        if record is None:
            raise ApprovalError(
                "unknown approval", location="approval.store",
                rule_id="SEC-APPROVAL")
        return record
