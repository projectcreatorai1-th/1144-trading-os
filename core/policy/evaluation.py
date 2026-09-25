"""Policy evaluation contract + deterministic evaluator (owned by core.policy).

The evaluator interprets a declarative rule DSL carried BY THE POLICY
(single source of truth for limits). Rules resolve fields from the risk
context by dotted path, compare canonical decimals, and map to permissions.
Same policy version + same context + same environment -> identical result
and hashes (no clock, no randomness, SECTION 7/22).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer

CONTRACT_VERSION = "1.0.0"

OPERATORS = ("<=", ">=", "<", ">", "==", "!=")
PERMISSION_RESULTS = ("ALLOW", "LIMITED", "BLOCK", "CLOSE_ONLY", "EMERGENCY")


def canonical_hash(value: Mapping[str, Any]) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def policy_content_hash(policy: Any) -> str:
    """Hash of the semantic policy content (conditions/actions/limits/type/scope)."""
    return canonical_hash({
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_type": policy.policy_type.value,
        "scope": policy.scope,
        "conditions": [dict(c) for c in policy.conditions],
        "actions": [dict(a) for a in policy.actions],
        "limits": dict(policy.limits),
    })


@dataclass(frozen=True)
class TriggeredRule:
    rule_id: str
    dimension: str
    field: str
    operator: str
    limit_name: str
    value: str
    limit_value: str
    outcome: str  # resulting permission contribution
    constraint: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dimension": self.dimension,
            "field": self.field,
            "operator": self.operator,
            "limit": self.limit_name,
            "value": self.value,
            "limit_value": self.limit_value,
            "outcome": self.outcome,
            "constraint": dict(self.constraint) if self.constraint else None,
        }


@dataclass(frozen=True)
class PolicyEvaluation:
    evaluation_id: str
    policy_id: str
    policy_version: str
    result: str
    triggered_rules: tuple[Mapping[str, Any], ...]
    failed_rules: tuple[str, ...]
    warnings: tuple[str, ...]
    context_hash: str
    policy_hash: str
    timestamp: datetime
    environment: str
    correlation_id: str
    causation_id: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("policy_evaluation_id", self.evaluation_id, location="evaluation.evaluation_id")
        validate_identifier("policy_id", self.policy_id, location="evaluation.policy_id")
        SemVer.parse(self.policy_version, location="evaluation.policy_version")
        if self.result not in PERMISSION_RESULTS:
            raise ContractValidationError(
                f"evaluation.result must be one of {PERMISSION_RESULTS}, got {self.result!r}",
                location="evaluation.result",
                rule_id="SCHEMA-ENUM",
            )
        for name, items in (("triggered_rules", self.triggered_rules), ("warnings", self.warnings)):
            if not isinstance(items, tuple):
                raise ContractValidationError(
                    f"evaluation.{name} must be a tuple",
                    location=f"evaluation.{name}",
                )
        if not isinstance(self.failed_rules, tuple):
            raise ContractValidationError(
                "evaluation.failed_rules must be a tuple",
                location="evaluation.failed_rules",
            )
        for hash_name in ("context_hash", "policy_hash"):
            value = getattr(self, hash_name)
            if not isinstance(value, str) or len(value) != 64:
                raise ContractValidationError(
                    f"evaluation.{hash_name} must be a sha-256 hex string",
                    location=f"evaluation.{hash_name}",
                )
        ensure_utc(self.timestamp, location="evaluation.timestamp")
        parse_environment(self.environment, location="evaluation.environment")
        from architecture.contracts.identifiers import validate_any_identifier

        validate_any_identifier(self.correlation_id, location="evaluation.correlation_id")
        if self.causation_id is not None:
            validate_any_identifier(self.causation_id, location="evaluation.causation_id")
        SemVer.parse(self.schema_version, location="evaluation.schema_version")


def _resolve(context: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    """Resolve a dotted field path against the flattened context; returns
    (present, value)."""
    node: Any = context
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


class PolicyEvaluator:
    """Deterministic rule interpreter. Rules live in policy.conditions:

    {"rule_id": "R-EXP-01", "dimension": "EXPOSURE", "field": "positions.gross_exposure",
     "op": "<=", "limit": "max_gross_exposure", "on_trigger": "BLOCK",
     "constraint": {...} (for LIMITED), "critical": true}
    - unresolvable limit reference -> failed rule (fail closed)
    - unresolvable/None field -> critical: BLOCK; else warning
    """

    def __init__(self, precedence: tuple[str, ...] | None = None) -> None:
        # precedence resolves from risk-config.yaml (single source) via the
        # kernel registry loader unless explicitly injected
        if precedence is None:
            from architecture.contracts.registry import load_registry

            precedence = tuple(str(p) for p in load_registry("risk-config.yaml")["permission_precedence"])
        self._precedence = precedence

    def evaluate(
        self,
        *,
        policy: Any,
        context: Mapping[str, Any],
        environment: str,
        timestamp: datetime,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> PolicyEvaluation:
        parse_environment(environment, location="evaluation.environment")
        if policy.environment is not None and policy.environment != environment:
            # policy scoped to another environment cannot authorize here
            return self._fail_closed(policy, context, environment, timestamp, correlation_id,
                                     causation_id, reason="policy_environment_mismatch")
        context_hash = canonical_hash(dict(context))
        phash = policy_content_hash(policy)
        triggered: list[Mapping[str, Any]] = []
        failed: list[str] = []
        warnings: list[str] = []
        outcomes: list[str] = []

        for condition in policy.conditions:
            rule = dict(condition)
            rule_id = str(rule.get("rule_id", ""))
            dimension = str(rule.get("dimension", "GENERAL"))
            field_path = str(rule.get("field", ""))
            operator = str(rule.get("op", "<="))
            limit_name = str(rule.get("limit", ""))
            on_trigger = str(rule.get("on_trigger", "BLOCK"))
            critical = bool(rule.get("critical", False))
            constraint = rule.get("constraint")

            if operator not in OPERATORS:
                failed.append(rule_id or f"invalid-operator:{operator}")
                outcomes.append("BLOCK")
                continue
            if limit_name not in policy.limits:
                failed.append(rule_id or f"unknown-limit:{limit_name}")
                outcomes.append("BLOCK")
                continue
            if on_trigger not in PERMISSION_RESULTS:
                failed.append(rule_id or f"invalid-outcome:{on_trigger}")
                outcomes.append("BLOCK")
                continue

            present, value = _resolve(context, field_path)
            if not present or value is None or value == "UNKNOWN":
                if critical:
                    outcomes.append("BLOCK")
                    triggered.append(TriggeredRule(
                        rule_id=rule_id, dimension=dimension, field=field_path,
                        operator=operator, limit_name=limit_name,
                        value="UNKNOWN", limit_value=str(policy.limits[limit_name]),
                        outcome="BLOCK",
                    ).to_dict())
                else:
                    warnings.append(f"{rule_id}:unknown_field:{field_path}")
                continue

            from core.ledger.money import parse_decimal

            try:
                left = parse_decimal(value, location=f"rule.{rule_id}.value")
                right = parse_decimal(policy.limits[limit_name], location=f"rule.{rule_id}.limit")
                numeric = True
            except ContractValidationError:
                numeric = False
                if operator not in ("==", "!=") or not isinstance(value, str):
                    failed.append(rule_id)
                    outcomes.append("BLOCK")
                    continue
                left, right = value, str(policy.limits[limit_name])

            if not self._compare(left, right, operator):
                outcomes.append(on_trigger)
                triggered.append(TriggeredRule(
                    rule_id=rule_id, dimension=dimension, field=field_path,
                    operator=operator, limit_name=limit_name,
                    value=str(left), limit_value=str(right),
                    outcome=on_trigger, constraint=constraint,
                ).to_dict())

        result = self._compose(outcomes)
        evaluation = PolicyEvaluation(
            evaluation_id=new_identifier("policy_evaluation_id"),
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            result=result,
            triggered_rules=tuple(triggered),
            failed_rules=tuple(failed),
            warnings=tuple(warnings),
            context_hash=context_hash,
            policy_hash=phash,
            timestamp=ensure_utc(timestamp, location="evaluation.timestamp"),
            environment=environment,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        evaluation.validate()
        return evaluation

    def _compose(self, outcomes: list[str]) -> str:
        """Most severe outcome wins (precedence from risk-config)."""
        if not outcomes:
            return "ALLOW"
        rank = {permission: index for index, permission in enumerate(self._precedence)}
        return min(outcomes, key=lambda outcome: rank.get(outcome, 0))

    @staticmethod
    def _compare(left: Any, right: Any, operator: str) -> bool:
        if operator == "<=":
            return left <= right
        if operator == ">=":
            return left >= right
        if operator == "<":
            return left < right
        if operator == ">":
            return left > right
        if operator == "==":
            return left == right
        return left != right

    def _fail_closed(self, policy, context, environment, timestamp, correlation_id,
                     causation_id, reason: str) -> PolicyEvaluation:
        evaluation = PolicyEvaluation(
            evaluation_id=new_identifier("policy_evaluation_id"),
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            result="BLOCK",
            triggered_rules=(),
            failed_rules=(reason,),
            warnings=(),
            context_hash=canonical_hash(dict(context)),
            policy_hash=policy_content_hash(policy),
            timestamp=ensure_utc(timestamp, location="evaluation.timestamp"),
            environment=environment,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        evaluation.validate()
        return evaluation
