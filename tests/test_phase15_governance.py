"""Phases 15/15B/18 - config governance + promotion pipeline."""
from __future__ import annotations

from datetime import timedelta

import pytest

from architecture.contracts.errors import ContractError
from architecture.contracts.time import utc_now
from core.policy.config_governance import (
    ConfigGovernor,
    ConfigStatus,
)
from core.strategy.promotion import (
    PromotionEvidence,
    PromotionLedger,
    STAGES,
)
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository


class FakeAudit(AuditRepository):
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)

    def verify(self):
        return {"records": len(self.records)}

    def get_by_id(self, audit_id):
        return None

    def iter_by_correlation_id(self, correlation_id):
        return iter([r for r in self.records
                     if r.correlation_id == correlation_id])


class TestConfigGovernance:
    def test_lifecycle_review_approve_activate_rollback(self):
        governor = ConfigGovernor(FakeAudit())
        config = governor.create(kind="risk_limits",
                                 content={"max_position": "0.5"},
                                 created_by="risk_manager")
        assert config.status is ConfigStatus.DRAFT
        assert len(config.content_sha256) == 64

        config = governor.transition(config.config_id, config.version,
                                      ConfigStatus.REVIEWED, actor="rm1")
        config = governor.transition(config.config_id, config.version,
                                      ConfigStatus.APPROVED, actor="approver1")
        assert config.approved_by == "approver1"
        config = governor.transition(config.config_id, config.version,
                                      ConfigStatus.ACTIVE, actor="approver1")
        assert config.effective_from is not None
        config = governor.transition(config.config_id, config.version,
                                      ConfigStatus.ROLLED_BACK,
                                      actor="approver1")
        assert config.effective_to is not None

    def test_illegal_transition_and_skip_rejected(self):
        governor = ConfigGovernor(FakeAudit())
        config = governor.create(kind="symbol_rules", content={"a": 1},
                                 created_by="u")
        with pytest.raises(ContractError):      # DRAFT -> ACTIVE illegal
            governor.transition(config.config_id, config.version,
                                ConfigStatus.ACTIVE, actor="u")

    def test_historical_reconstruction(self):
        governor = ConfigGovernor(FakeAudit())
        base = utc_now() - timedelta(hours=2)
        v1 = governor.create(kind="news_rules", content={"v": 1},
                             created_by="u", at=base)
        for target in (ConfigStatus.REVIEWED, ConfigStatus.APPROVED,
                       ConfigStatus.ACTIVE):
            v1 = governor.transition(v1.config_id, v1.version, target,
                                     actor="u", at=base)
        later = base + timedelta(hours=1)
        v2 = governor.create(kind="news_rules", content={"v": 2},
                             created_by="u", at=later)
        for target in (ConfigStatus.REVIEWED, ConfigStatus.APPROVED,
                       ConfigStatus.ACTIVE):
            v2 = governor.transition(v2.config_id, v2.version, target,
                                     actor="u", at=later)
        governor.transition(v2.config_id, v2.version, ConfigStatus.ROLLED_BACK,
                            actor="u", at=later + timedelta(minutes=5))

        during_v1 = base + timedelta(minutes=30)
        during_v2 = later + timedelta(minutes=2)
        after_rollback = later + timedelta(minutes=10)
        assert governor.active_at("news_rules", during_v1).content == {"v": 1}
        assert governor.active_at("news_rules", during_v2).content == {"v": 2}
        assert governor.active_at("news_rules", after_rollback).content \
            == {"v": 1}   # after v2 rollback, v1 is the active window again

    def test_requires_audit(self):
        with pytest.raises(ContractError):
            ConfigGovernor(audit=object())


def _evidence(strategy_id, stage, **overrides):
    fields = dict(
        strategy_id=strategy_id, strategy_version="1.0.0", stage=stage,
        dataset_ref="ds_1", configuration_ref="cfg_1",
        risk_profile_ref="risk_1",
        performance={"sharpe": 1.2, "max_drawdown": "0.08"},
        failure_tests_passed=True, regression_passed=True,
        operator_approval="operator_bank", recorded_at=utc_now())
    fields.update(overrides)
    return PromotionEvidence(**fields)


class TestPromotionPipeline:
    def test_ordered_promotion_to_live_candidate(self):
        ledger = PromotionLedger(FakeAudit())
        strategy_id = "strat_alpha"
        for stage in STAGES:
            evidence = ledger.record(_evidence(strategy_id, stage))
            assert evidence.evidence_sha256
        assert ledger.stage_of(strategy_id) == "LIVE_CANDIDATE"
        assert len(ledger.history(strategy_id)) == len(STAGES)

    def test_no_stage_skipping(self):
        ledger = PromotionLedger(FakeAudit())
        ledger.record(_evidence("strat_beta", "DRAFT"))
        with pytest.raises(ContractError):
            ledger.record(_evidence("strat_beta", "DEMO"))  # skips stages

    def test_evidence_gates_enforced(self):
        ledger = PromotionLedger(FakeAudit())
        with pytest.raises(ContractError):     # failed failure-tests
            ledger.record(_evidence("s", "DRAFT",
                                    failure_tests_passed=False))
        with pytest.raises(ContractError):     # no operator approval
            ledger.record(_evidence("s", "DRAFT", operator_approval=""))
        with pytest.raises(ContractError):     # empty performance
            ledger.record(_evidence("s", "DRAFT", performance={}))

    def test_unknown_stage_rejected(self):
        with pytest.raises(ContractError):
            _evidence("s", "LIVE_ENABLED").validate()
