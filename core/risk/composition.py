"""Risk composition (owned by core.risk).

Deterministic aggregation across dimension/safety evaluations: the most
severe permission wins (precedence from risk-config.yaml - the only place
precedence exists). LIMITED constraints intersect (strictest value per key).
UNKNOWN critical fields gate the composition to BLOCK before any evaluation
(SECTIONS 15/16)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError
from core.ledger.money import parse_decimal
from core.risk.config import critical_unknown_fields, severity_rank
from core.risk.context import RiskContext

CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class CompositionResult:
    permission: str
    triggered_rules: tuple[Mapping[str, Any], ...]
    constraints: Mapping[str, str]
    unknown_criticals: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.permission in ("ALLOW", "LIMITED")


def check_critical_unknowns(context: RiskContext) -> tuple[str, ...]:
    """Critical context fields that are UNKNOWN (None/'UNKNOWN') -> the
    composition must BLOCK (UNKNOWN is never safe)."""
    unknown: list[str] = []
    flattened = context.flattened()
    for dotted in critical_unknown_fields():
        node: Any = flattened
        present = True
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                present = False
                break
            node = node[part]
        if not present or node is None or node == "UNKNOWN":
            unknown.append(dotted)
    return tuple(unknown)


def compose(evaluations: Iterable[Any], context: RiskContext) -> CompositionResult:
    """ evaluations: objects with .result and .triggered_rules (PolicyEvaluation
    or equivalent). Most severe result wins; LIMITED constraints intersect."""
    unknown_criticals = check_critical_unknowns(context)
    if unknown_criticals:
        return CompositionResult(
            permission="BLOCK",
            triggered_rules=(),
            constraints={},
            unknown_criticals=unknown_criticals,
        )

    final_permission = "ALLOW"
    triggered: list[Mapping[str, Any]] = []
    constraints: dict[str, str] = {}
    for evaluation in evaluations:
        triggered.extend(dict(rule) for rule in evaluation.triggered_rules)
        if severity_rank(evaluation.result) < severity_rank(final_permission):
            final_permission = evaluation.result
        for rule in evaluation.triggered_rules:
            constraint = (rule or {}).get("constraint")
            if constraint and rule.get("outcome") == "LIMITED":
                for key, value in dict(constraint).items():
                    incoming = str(value)
                    if key not in constraints:
                        constraints[key] = incoming
                    else:
                        constraints[key] = _stricter(constraints[key], incoming)
    return CompositionResult(
        permission=final_permission,
        triggered_rules=tuple(triggered),
        constraints=dict(constraints) if final_permission == "LIMITED" else {},
        unknown_criticals=(),
    )


def _stricter(current: str, incoming: str) -> str:
    """Strictest numeric constraint wins (min); non-numeric equality keeps current."""
    try:
        return str(min(parse_decimal(current, location="compose.current"),
                       parse_decimal(incoming, location="compose.incoming")))
    except ContractValidationError:
        return current
