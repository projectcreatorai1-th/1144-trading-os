"""Risk configuration resolver (owned by core.risk).

Single source of truth: architecture/risk-config.yaml - permission
precedence, decision TTL, hard-limit authority, critical unknown fields.
Nothing hard-codes these (SECTION 15/27)."""
from __future__ import annotations

from functools import lru_cache

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.registry import load_registry

CONTRACT_VERSION = "1.0.0"


@lru_cache(maxsize=None)
def permission_precedence() -> tuple[str, ...]:
    """Highest authority first (EMERGENCY > CLOSE_ONLY > BLOCK > LIMITED > ALLOW)."""
    data = load_registry("risk-config.yaml")
    precedence = tuple(str(p) for p in data["permission_precedence"])
    if len(set(precedence)) != len(precedence):
        raise ContractValidationError(
            "permission_precedence must not contain duplicates",
            location="risk-config.permission_precedence",
        )
    return precedence


@lru_cache(maxsize=None)
def decision_ttl_seconds() -> int:
    return int(load_registry("risk-config.yaml")["decision_ttl_seconds"])


@lru_cache(maxsize=None)
def hard_limit_override_allowed() -> bool:
    return bool(load_registry("risk-config.yaml").get("hard_limit_override_allowed", False))


@lru_cache(maxsize=None)
def critical_unknown_fields() -> tuple[str, ...]:
    return tuple(str(f) for f in load_registry("risk-config.yaml")["critical_unknown_fields"])


@lru_cache(maxsize=None)
def risk_rule_version() -> str:
    return str(load_registry("risk-config.yaml").get("risk_rule_version", "1.0.0"))


def severity_rank(permission: str) -> int:
    """Lower rank = more severe (single source: risk-config precedence)."""
    precedence = permission_precedence()
    if permission not in precedence:
        raise ContractValidationError(
            f"Unknown permission '{permission}' (no precedence entry -> fail closed)",
            location="risk.precedence",
            rule_id="RISK-002",
            details={"precedence": list(precedence)},
        )
    return precedence.index(permission)
