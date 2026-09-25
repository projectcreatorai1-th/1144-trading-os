"""Strategy promotion pipeline (core.strategy, Phases 15B + 18).

Immutable, evidence-carrying promotion: DRAFT -> VALIDATED -> SIMULATION ->
DEMO -> SHADOW -> CANARY -> LIVE-CANDIDATE. No stage may be skipped, no
evidence may be asserted without its bundle fields, and LIVE-CANDIDATE is
an EVIDENCE STATE ONLY - it never enables LIVE execution (that requires
the explicit human-controlled operation of the LIVE hard gate).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

STAGES = ("DRAFT", "VALIDATED", "SIMULATION", "DEMO", "SHADOW",
          "CANARY", "LIVE_CANDIDATE")


@dataclass(frozen=True)
class PromotionEvidence:
    """§21.1 promotion evidence bundle for one strategy at one stage."""

    strategy_id: str
    strategy_version: str
    stage: str
    dataset_ref: str
    configuration_ref: str
    risk_profile_ref: str
    performance: Mapping[str, Any]
    failure_tests_passed: bool
    regression_passed: bool
    operator_approval: str
    recorded_at: datetime
    evidence_sha256: str = ""

    def validate(self) -> None:
        if self.stage not in STAGES:
            raise ContractError(
                f"unknown promotion stage {self.stage!r}",
                location="promotion.stage", rule_id="PRM-001",
                details={"stages": list(STAGES)})
        for name in ("strategy_id", "strategy_version", "dataset_ref",
                     "configuration_ref", "risk_profile_ref"):
            if not getattr(self, name):
                raise ContractError(f"promotion.{name} required",
                                    location=f"promotion.{name}",
                                    rule_id="PRM-002")
        if not self.performance:
            raise ContractError(
                "promotion.performance evidence required (no numbers, "
                "no promotion)",
                location="promotion.performance", rule_id="PRM-002")
        if not (self.failure_tests_passed and self.regression_passed):
            raise ContractError(
                "promotion requires failure tests AND regression to pass",
                location="promotion.gates", rule_id="PRM-003")
        if not self.operator_approval:
            raise ContractError(
                "promotion requires an operator approval id",
                location="promotion.approval", rule_id="PRM-004")
        ensure_utc(self.recorded_at, location="promotion.recorded_at")

    def with_hash(self) -> "PromotionEvidence":
        payload = json.dumps(
            {"strategy_id": self.strategy_id,
             "strategy_version": self.strategy_version,
             "stage": self.stage, "dataset_ref": self.dataset_ref,
             "performance": dict(self.performance)},
            sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return PromotionEvidence(
            **{**self.__dict__, "evidence_sha256": digest})


class PromotionLedger:
    """Ordered, audited promotion records. REUSES the strategy store ids
    and the audit repository; it never mutates strategy state directly."""

    def __init__(self, audit: AuditRepository) -> None:
        if not isinstance(audit, AuditRepository):
            raise ContractError("PromotionLedger requires an AuditRepository",
                                location="promotion.init", rule_id="PRM-005")
        self._audit = audit
        self._records: dict[str, list[PromotionEvidence]] = {}

    def record(self, evidence: PromotionEvidence) -> PromotionEvidence:
        evidence = evidence.with_hash()
        evidence.validate()
        history = self._records.setdefault(evidence.strategy_id, [])
        if history:
            current_stage = history[-1].stage
            expected = STAGES[STAGES.index(current_stage) + 1]
            if evidence.stage != expected:
                raise ContractError(
                    f"promotion must advance {current_stage} -> {expected}, "
                    f"got {evidence.stage} (no skipping, no shortcuts)",
                    location="promotion.order", rule_id="PRM-006",
                    details={"current": current_stage,
                             "expected": expected,
                             "got": evidence.stage})
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=evidence.operator_approval,
            action="STRATEGY_PROMOTION_EVIDENCE",
            entity_type="strategy",
            entity_id=f"{evidence.strategy_id}@{evidence.strategy_version}",
            event_time=ensure_utc(evidence.recorded_at,
                                  location="promotion.audit"),
            before=None,
            after={"stage": evidence.stage,
                   "evidence_sha256": evidence.evidence_sha256,
                   "dataset": evidence.dataset_ref},
            reason=f"promotion evidence to {evidence.stage}",
            source="core.strategy.promotion",
            environment="SIMULATION",
            correlation_id=evidence.strategy_id))
        history.append(evidence)
        return evidence

    def stage_of(self, strategy_id: str) -> str | None:
        history = self._records.get(strategy_id)
        return history[-1].stage if history else None

    def history(self, strategy_id: str) -> tuple[PromotionEvidence, ...]:
        return tuple(self._records.get(strategy_id, ()))
