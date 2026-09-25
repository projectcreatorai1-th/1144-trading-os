"""Causal trace contract (SECTION 24).

correlation_id groups one causal chain; causation_id points at the direct
cause. The chain NEWS -> EVENT -> MARKET STATE -> AI -> STRATEGY -> POLICY ->
RISK -> ORDER -> EXECUTION -> POSITION -> RESULT must stay linkable.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from architecture.contracts.errors import CausalityError
from architecture.contracts.identifiers import is_valid_identifier


class CausalNode(Protocol):
    """Anything with identity and causal links (Event, Decision, Order, ...)."""

    @property
    def event_id(self) -> str: ...  # noqa: E704 - protocol style


class _NodeAdapter:
    __slots__ = ("node_id", "correlation_id", "causation_id")

    def __init__(self, node_id: str, correlation_id: Any, causation_id: Any) -> None:
        self.node_id = node_id
        self.correlation_id = correlation_id
        self.causation_id = causation_id


_NODE_ID_ATTRIBUTES = (
    "event_id",
    "decision_id",
    "risk_decision_id",
    "order_id",
    "position_id",
    "ledger_entry_id",
    "audit_id",
    "id",
)


def _primary_id_attribute(node: Any) -> str | None:
    """A node's own identity is the FIRST declared dataclass field ending in
    '_id' (event_id, order_id, ...) - never a reference field such as an
    order's risk_decision_id."""
    fields = getattr(type(node), "__dataclass_fields__", None)
    if fields:
        for name in fields:  # declaration order
            if name.endswith("_id"):
                return name
        return None
    for attribute in _NODE_ID_ATTRIBUTES:
        if getattr(node, attribute, None) is not None:
            return attribute
    return None


def _adapt(node: Any, index: int) -> _NodeAdapter:
    attribute = _primary_id_attribute(node)
    node_id = getattr(node, attribute, None) if attribute else None
    if not isinstance(node_id, str) or not node_id:
        raise CausalityError(
            f"Chain node #{index} has no usable identifier "
            "(expected a primary id field ending in '_id')",
            location=f"chain[{index}]",
            details={"type": type(node).__name__},
        )
    return _NodeAdapter(
        node_id,
        getattr(node, "correlation_id", None),
        getattr(node, "causation_id", None),
    )


def validate_causal_chain(nodes: Iterable[Any]) -> list[_NodeAdapter]:
    """Validate a causal chain and return the adapted nodes.

    Rules:
    - every node must carry a non-empty correlation_id
    - causation_id must be None or reference a node id within the chain
    - causation links must not form cycles
    - exactly one chain root (causation_id None) per correlation chain
    """
    adapted = [_adapt(node, i) for i, node in enumerate(nodes)]

    for index, node in enumerate(adapted):
        if not isinstance(node.correlation_id, str) or not node.correlation_id:
            raise CausalityError(
                f"Node {node.node_id} is missing correlation_id",
                location=f"chain[{index}]",
                rule_id="TRACE-001",
            )

    ids = [node.node_id for node in adapted]
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise CausalityError(
            f"Duplicate node ids in chain: {duplicated}",
            location="chain",
        )

    id_set = set(ids)
    for index, node in enumerate(adapted):
        if node.causation_id is None:
            continue
        if not isinstance(node.causation_id, str):
            raise CausalityError(
                f"Node {node.node_id} has non-string causation_id",
                location=f"chain[{index}]",
            )
        if node.causation_id not in id_set:
            raise CausalityError(
                f"Node {node.node_id} references unknown causation_id {node.causation_id}",
                location=f"chain[{index}]",
                details={"causation_id": node.causation_id, "known_ids": sorted(id_set)},
            )

    _ensure_acyclic(adapted)

    correlations = {node.correlation_id for node in adapted}
    if len(correlations) > 1:
        raise CausalityError(
            "One causal chain must share a single correlation_id "
            f"(found {len(correlations)})",
            location="chain",
            details={"correlation_ids": sorted(correlations)},
        )

    roots = [node for node in adapted if node.causation_id is None]
    if not roots:
        raise CausalityError(
            "Causal chain has no root (every node has a causation_id; cycle or broken chain)",
            location="chain",
        )
    if len(roots) > 1:
        raise CausalityError(
            f"Causal chain must have exactly one root, found {len(roots)}",
            location="chain",
            details={"roots": [node.node_id for node in roots]},
        )
    return adapted


def _ensure_acyclic(adapted: list[_NodeAdapter]) -> None:
    id_to_node = {node.node_id: node for node in adapted}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in done:
            return
        if node_id in visiting:
            raise CausalityError(
                f"Causation cycle detected involving {node_id}",
                location="chain",
                details={"node_id": node_id},
            )
        visiting.add(node_id)
        node = id_to_node[node_id]
        if node.causation_id is not None and node.causation_id in id_to_node:
            visit(node.causation_id)
        visiting.discard(node_id)
        done.add(node_id)

    for node in adapted:
        visit(node.node_id)


def is_valid_event_reference(value: Any) -> bool:
    """True when value is a well-formed event_id reference (evt_ prefix)."""
    return isinstance(value, str) and is_valid_identifier("event_id", value)
