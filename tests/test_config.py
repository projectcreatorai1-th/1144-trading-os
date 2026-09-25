"""Configuration envelope tests (SECTION 37) - including security failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.config import ConfigContract, ConfigKind
from architecture.contracts.errors import ContractValidationError, IdentifierValidationError
from tests.factories import at, make_config


class TestConfigContract:
    def test_valid_system_config(self):
        make_config().validate()

    def test_strategy_config_with_environment(self):
        make_config(
            kind=ConfigKind.STRATEGY, environment="PAPER", data={"grid": {"levels": 5}}
        ).validate()

    def test_risk_config_requires_environment(self):
        make_config(
            kind=ConfigKind.RISK, environment="LIVE", data={"max_exposure": 10000}
        ).validate()

    def test_kinds_are_separated(self):
        assert {k.value for k in ConfigKind} == {
            "SYSTEM", "ENVIRONMENT", "STRATEGY", "RISK", "USER_PREFERENCES"
        }


class TestConfigFailures:
    def test_strategy_config_without_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_config(kind=ConfigKind.STRATEGY).validate()

    def test_risk_config_without_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_config(kind=ConfigKind.RISK).validate()

    def test_secret_like_key_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            make_config(data={"api_key": "abc123"}).validate()
        assert excinfo.value.rule_id == "SEC-001"

    def test_password_like_key_rejected(self):
        with pytest.raises(ContractValidationError):
            make_config(data={"broker_password": "hunter2"}).validate()

    def test_invalid_version_rejected(self):
        with pytest.raises(ContractValidationError):
            make_config(version="latest").validate()

    def test_invalid_config_id_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_config(config_id="cfg-1").validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_config(kind=ConfigKind.STRATEGY, environment="PROD").validate()

    def test_config_is_immutable(self):
        config = make_config()
        with pytest.raises(FrozenInstanceError):
            config.data = {"grid": {"levels": 99}}
