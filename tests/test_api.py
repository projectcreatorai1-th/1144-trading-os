"""API contract tests (SECTION 19) - including failure tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from architecture.contracts.errors import ContractValidationError, IdentifierValidationError
from platform.api.contracts import APIResponse, EndpointRegistry, build_endpoint_registry
from tests.factories import at, make_api_request, make_api_response

REGISTRY = build_endpoint_registry()

CORE_ENDPOINTS = {
    ("GET", "/health"),
    ("GET", "/system/state"),
    ("GET", "/environment"),
    ("GET", "/contracts"),
    ("GET", "/schemas"),
    ("GET", "/events"),
    ("GET", "/audit"),
}

NAMESPACES = {
    "/market", "/news", "/intelligence", "/strategy", "/policy", "/risk",
    "/portfolio", "/orders", "/positions", "/ledger", "/research",
    "/backtest", "/replay", "/operations",
}


class TestEndpointRegistry:
    def test_core_endpoints_registered(self):
        assert {(e["method"], e["path"]) for e in REGISTRY.endpoints} == CORE_ENDPOINTS

    def test_namespaces_reserved(self):
        assert set(REGISTRY.namespaces) == NAMESPACES

    def test_every_endpoint_declares_permission_and_scope(self):
        for endpoint in REGISTRY.endpoints:
            assert endpoint["permission"]
            assert endpoint["environment_scope"]

    def test_duplicate_endpoints_rejected(self):
        with pytest.raises(ContractValidationError) as excinfo:
            EndpointRegistry(
                endpoints=(
                    {"method": "GET", "path": "/health", "permission": "VIEW", "environment_scope": "all"},
                    {"method": "GET", "path": "/health", "permission": "VIEW", "environment_scope": "all"},
                ),
                namespaces=(),
                version="1.0.0",
            )
        assert excinfo.value.rule_id == "ARCH-003"


class TestRequestChecks:
    def test_valid_request_passes(self):
        REGISTRY.check_request(make_api_request())

    def test_namespace_request_allowed(self):
        request = make_api_request(path="/orders", method="GET", permission="VIEW")
        REGISTRY.check_request(request)

    def test_unregistered_path_rejected(self):
        request = make_api_request(path="/trade/now")
        with pytest.raises(ContractValidationError) as excinfo:
            REGISTRY.check_request(request)
        assert excinfo.value.rule_id == "ARCH-008"

    def test_wrong_permission_rejected(self):
        request = make_api_request(permission="ADMIN")
        with pytest.raises(ContractValidationError):
            REGISTRY.check_request(request)

    def test_unknown_method_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_request(method="FETCH").validate()

    def test_invalid_actor_rejected(self):
        with pytest.raises(IdentifierValidationError):
            make_api_request(actor_id="anonymous").validate()

    def test_unknown_environment_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_request(environment="SANDBOX").validate()


class TestResponseContract:
    def test_valid_ok_response(self):
        make_api_response().validate()

    def test_valid_error_response(self):
        make_api_response(
            status="ERROR", data=None, error_code="NOT_FOUND",
            error_message="resource not found",
        ).validate()

    def test_ok_with_error_code_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_response(error_code="NOT_FOUND").validate()

    def test_error_without_code_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_response(status="ERROR", data=None, error_message="boom").validate()

    def test_error_with_unknown_code_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_response(
                status="ERROR", data=None, error_code="OOK", error_message="x"
            ).validate()

    def test_error_without_message_rejected(self):
        with pytest.raises(ContractValidationError):
            make_api_response(status="ERROR", data=None, error_code="NOT_FOUND").validate()

    def test_response_must_carry_contract_versions(self):
        with pytest.raises(ContractValidationError):
            make_api_response(contract_versions={}).validate()

    def test_response_is_immutable(self):
        response = make_api_response()
        with pytest.raises(FrozenInstanceError):
            response.status = "ERROR"
