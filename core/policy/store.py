"""Policy store ports (owned by core.policy).

Policies are versioned documents: semantic changes create a NEW version;
historical versions are immutable and forever queryable (SECTION 5/26).
Implementations live in platform.database."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator

from core.policy.contracts import Policy
from core.policy.evaluation import PolicyEvaluation


class PolicyStore(ABC):
    @abstractmethod
    def save(self, policy: Policy) -> None:  # pragma: no cover - port definition
        """Append a policy version (policy_id + version unique; no overwrite)."""
        ...

    @abstractmethod
    def get_version(self, policy_id: str, policy_version: str) -> Policy:  # pragma: no cover
        ...

    @abstractmethod
    def iter_versions(self, policy_id: str) -> Iterator[Policy]:  # pragma: no cover
        ...

    @abstractmethod
    def iter_all_latest(self) -> Iterator[Policy]:  # pragma: no cover
        """Latest version of every policy (any status)."""
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class PolicyEvaluationStore(ABC):
    @abstractmethod
    def append(self, evaluation: PolicyEvaluation) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_by_id(self, evaluation_id: str) -> PolicyEvaluation:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[PolicyEvaluation]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
