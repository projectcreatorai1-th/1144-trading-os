"""Strategy dependency graph (owned by core.strategy).

Answers "what does this strategy depend on?" and "if data source X breaks,
which strategies are affected?". Cycles fail closed. The graph is an
analysis structure - never an execution graph (SECTION 16)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from architecture.contracts.errors import ContractError

CONTRACT_VERSION = "1.0.0"

DEPENDENCY_KINDS = ("DATA_SOURCE", "SYMBOL", "POLICY", "RISK_BUDGET", "FEATURE", "EVENT")


class DependencyCycleError(ContractError):
    rule_id = "STRATEGY-003"


@dataclass
class _Node:
    kind: str
    key: str
    edges: set[tuple[str, str]] = field(default_factory=set)  # nodes this node depends on


class DependencyGraph:
    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], _Node] = {}

    def add_node(self, kind: str, key: str) -> None:
        self._validate_kind(kind)
        self._nodes.setdefault((kind, key), _Node(kind, key))

    def add_dependency(self, kind: str, key: str, depends_on_kind: str, depends_on_key: str) -> None:
        self.add_node(kind, key)
        self.add_node(depends_on_kind, depends_on_key)
        self._nodes[(kind, key)].edges.add((depends_on_kind, depends_on_key))
        self.check_cycles()

    def dependencies_of(self, kind: str, key: str) -> tuple[tuple[str, str], ...]:
        node = self._nodes.get((kind, key))
        return tuple(sorted(node.edges)) if node else ()

    def impacted_by(self, kind: str, key: str) -> tuple[tuple[str, str], ...]:
        """Which strategy nodes (transitively) depend on the given node."""
        impacted: set[tuple[str, str]] = set()
        frontier = {(kind, key)}
        changed = True
        while changed:
            changed = False
            for node_key, node in self._nodes.items():
                if node_key in impacted or node_key in frontier:
                    continue
                if node.edges & (impacted | frontier):
                    impacted.add(node_key)
                    changed = True
        return tuple(sorted(
            node_key for node_key in impacted if self._nodes[node_key].kind == "STRATEGY"
        ))

    def check_cycles(self) -> None:
        state: dict[tuple[str, str], int] = {}

        def visit(node_key: tuple[str, str]) -> None:
            if state.get(node_key) == 2:
                return
            if state.get(node_key) == 1:
                raise DependencyCycleError(
                    f"Dependency cycle detected involving {node_key}",
                    location="strategy.dependency_graph",
                )
            state[node_key] = 1
            for edge in self._nodes.get(node_key, _Node("", "")).edges:
                visit(edge)
            state[node_key] = 2

        for node_key in list(self._nodes):
            visit(node_key)

    @staticmethod
    def _validate_kind(kind: str) -> None:
        if kind not in DEPENDENCY_KINDS + ("STRATEGY",):
            raise ContractError(
                f"Unknown dependency kind '{kind}'",
                location="strategy.dependency_graph", details={"kinds": DEPENDENCY_KINDS},
            )
