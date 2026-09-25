"""Data quality contract tests + AI adapter contract tests (SECTIONS 23/35)."""
from __future__ import annotations

import pytest

from adapters.ai.contracts import AIAnalysis, AIAnalysisType, Model, ModelVersion
from architecture.contracts.errors import ContractValidationError, IdentifierValidationError, TimeValidationError
from core.validation.contracts import DataQualityLevel, DataQualityReport
from tests.factories import at, make_ai_analysis, make_model, make_model_version


class TestDataQuality:
    def test_valid_report(self):
        report = DataQualityReport(level=DataQualityLevel.VERIFIED, checked_at=at(12, 0))
        report.validate()

    def test_levels_registered(self):
        assert {l.value for l in DataQualityLevel} == {
            "VERIFIED", "VALIDATED", "DEGRADED", "STALE", "INVALID", "UNKNOWN"
        }

    def test_fail_closed_levels(self):
        assert DataQualityLevel.UNKNOWN.blocks_risk_allowance
        assert DataQualityLevel.DEGRADED.blocks_risk_allowance
        assert DataQualityLevel.STALE.blocks_risk_allowance
        assert DataQualityLevel.INVALID.blocks_risk_allowance
        assert not DataQualityLevel.VERIFIED.blocks_risk_allowance
        assert not DataQualityLevel.VALIDATED.blocks_risk_allowance

    def test_invalid_never_flows_downstream(self):
        assert not DataQualityLevel.INVALID.allows_downstream_processing
        assert DataQualityLevel.STALE.allows_downstream_processing
        assert DataQualityLevel.VALIDATED.allows_downstream_processing

    def test_unknown_helper_report(self):
        report = DataQualityReport.unknown(at(12, 0), "feed down")
        report.validate()
        assert report.level is DataQualityLevel.UNKNOWN
        assert report.level.blocks_risk_allowance

    def test_invalid_level_rejected(self):
        with pytest.raises(ContractValidationError):
            DataQualityReport(level="FINE", checked_at=at(12, 0)).validate()

    def test_naive_checked_at_rejected(self):
        from datetime import datetime

        with pytest.raises(TimeValidationError):
            DataQualityReport(level=DataQualityLevel.VERIFIED, checked_at=datetime(2026, 9, 23)).validate()


class TestAIContracts:
    def test_valid_analysis(self):
        make_ai_analysis().validate()

    def test_analysis_types(self):
        assert {t.value for t in AIAnalysisType} == {
            "MARKET_REGIME", "NEWS_SENTIMENT", "STRATEGY_SUPPORT", "RISK_CONTEXT"
        }

    def test_confidence_bounds_enforced(self):
        with pytest.raises(ContractValidationError):
            make_ai_analysis(confidence=1.5).validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_ai_analysis(environment="TEST").validate()

    def test_model_and_version(self):
        model = make_model()
        model.validate()
        version = make_model_version()
        version.validate()

    def test_invalid_model_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            Model(model_id="gpt", name="classifier").validate()

    def test_invalid_model_version_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            ModelVersion(
                model_version_id="mv1", model_id=make_model().model_id,
                version="1.0.0", registered_at=at(8, 0),
            ).validate()

    def test_ai_module_never_depends_on_broker_or_mt5(self):
        import yaml

        from architecture.contracts.registry import find_project_root

        arch = yaml.safe_load(
            (find_project_root() / "architecture" / "architecture.yaml").read_text(encoding="utf-8")
        )
        ai = next(m for m in arch["modules"] if m["id"] == "adapters.ai")
        assert "adapters.mt5" in ai["forbidden_dependencies"]
        assert "adapters.broker" in ai["forbidden_dependencies"]
