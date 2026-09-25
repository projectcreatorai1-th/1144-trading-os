"""API contract (owned by platform.api).

The endpoint registry lives in architecture/api.yaml (single source of truth).
Phase 0 defines contracts only: no HTTP server. Requests carry identity,
environment and chain identifiers; responses carry structured errors and
contract versions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_any_identifier, validate_identifier
from architecture.contracts.registry import load_registry
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ApiStatus(Enum):
    OK = "OK"
    ERROR = "ERROR"


class ApiErrorCode(Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class APIRequest:
    request_id: str
    correlation_id: str
    method: str
    path: str
    environment: str
    actor_id: str
    permission: str
    request_time: datetime
    payload: Mapping[str, Any] | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("request_id", self.request_id, location="api.request_id")
        validate_any_identifier(self.correlation_id, location="api.correlation_id")
        if not isinstance(self.method, str) or self.method not in {m.value for m in HttpMethod}:
            raise ContractValidationError(
                f"api.method must be a known HTTP method, got {self.method!r}",
                location="api.method",
                rule_id="SCHEMA-ENUM",
            )
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ContractValidationError(
                "api.path must start with '/'",
                location="api.path",
            )
        parse_environment(self.environment, location="api.environment")
        validate_identifier("user_id", self.actor_id, location="api.actor_id")
        if not isinstance(self.permission, str) or not self.permission:
            raise ContractValidationError(
                "api.permission must be a non-empty string",
                location="api.permission",
            )
        ensure_utc(self.request_time, location="api.request_time")
        if self.payload is not None and not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "api.payload must be a mapping or None",
                location="api.payload",
            )
        SemVer.parse(self.contract_version, location="api.contract_version")


@dataclass(frozen=True)
class APIResponse:
    request_id: str
    correlation_id: str
    status: str
    response_time: datetime
    environment: str
    contract_versions: Mapping[str, str]
    data: Mapping[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("request_id", self.request_id, location="api.request_id")
        validate_any_identifier(self.correlation_id, location="api.correlation_id")
        if self.status not in {s.value for s in ApiStatus}:
            raise ContractValidationError(
                f"api.status must be OK or ERROR, got {self.status!r}",
                location="api.status",
                rule_id="SCHEMA-ENUM",
            )
        ensure_utc(self.response_time, location="api.response_time")
        parse_environment(self.environment, location="api.environment")
        if not isinstance(self.contract_versions, Mapping) or not self.contract_versions:
            raise ContractValidationError(
                "api.contract_versions must be a non-empty mapping (responses are versioned)",
                location="api.contract_versions",
            )
        if self.status == ApiStatus.OK.value:
            if self.error_code is not None:
                raise ContractValidationError(
                    "OK responses must not carry error_code",
                    location="api.error_code",
                )
        else:
            if self.error_code is None or self.error_code not in {c.value for c in ApiErrorCode}:
                raise ContractValidationError(
                    "ERROR responses require a valid error_code",
                    location="api.error_code",
                    rule_id="SCHEMA-ENUM",
                )
            if not isinstance(self.error_message, str) or not self.error_message:
                raise ContractValidationError(
                    "ERROR responses require error_message",
                    location="api.error_message",
                )


class EndpointRegistry:
    """Loads architecture/api.yaml and checks requests against it."""

    def __init__(
        self,
        endpoints: tuple[Mapping[str, Any], ...],
        namespaces: tuple[str, ...],
        version: str,
    ) -> None:
        self._endpoints = endpoints
        self._namespaces = namespaces
        self.version = version
        seen: set[tuple[str, str]] = set()
        for endpoint in endpoints:
            key = (endpoint["method"], endpoint["path"])
            if key in seen:
                raise ContractValidationError(
                    f"Duplicate endpoint {key[0]} {key[1]} in api registry",
                    location="api.registry",
                    rule_id="ARCH-003",
                )
            seen.add(key)

    @property
    def endpoints(self) -> tuple[Mapping[str, Any], ...]:
        return self._endpoints

    @property
    def namespaces(self) -> tuple[str, ...]:
        return self._namespaces

    def find(self, method: str, path: str) -> Mapping[str, Any] | None:
        for endpoint in self._endpoints:
            if endpoint["method"] == method and endpoint["path"] == path:
                return endpoint
        return None

    def check_request(self, request: APIRequest) -> None:
        """Endpoint must be registered or fall under a reserved namespace;
        the claimed permission must match the registered endpoint permission."""
        request.validate()
        endpoint = self.find(request.method, request.path)
        if endpoint is None:
            if not any(
                request.path == ns or request.path.startswith(ns + "/")
                for ns in self._namespaces
            ):
                raise ContractValidationError(
                    f"Endpoint {request.method} {request.path} is neither registered "
                    "nor under a reserved namespace",
                    location="api.registry",
                    rule_id="ARCH-008",
                    details={"reserved_namespaces": list(self._namespaces)},
                )
            return
        if request.permission != endpoint["permission"]:
            raise ContractValidationError(
                f"Permission mismatch for {request.method} {request.path}: "
                f"claimed '{request.permission}', endpoint requires '{endpoint['permission']}'",
                location="api.permission",
                details={"claimed": request.permission, "required": endpoint["permission"]},
            )


@lru_cache(maxsize=None)
def build_endpoint_registry() -> EndpointRegistry:
    data = load_registry("api.yaml")
    return EndpointRegistry(
        endpoints=tuple(data["endpoints"]),
        namespaces=tuple(data["reserved_namespaces"]),
        version=str(data["version"]),
    )
