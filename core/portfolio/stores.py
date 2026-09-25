"""Portfolio store ports (owned by core.portfolio)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.portfolio.allocation import CapitalAllocation
from core.portfolio.decision import PortfolioDecision
from core.portfolio.portfolio_contract import Portfolio, PortfolioMembership


class PortfolioStore(ABC):
    @abstractmethod
    def save(self, portfolio: Portfolio) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def get_version(self, portfolio_id: str, portfolio_version: str) -> Portfolio:  # pragma: no cover
        ...

    @abstractmethod
    def iter_all_latest(self) -> Iterator[Portfolio]:  # pragma: no cover
        ...


class MembershipStore(ABC):
    @abstractmethod
    def append(self, membership: PortfolioMembership) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def iter_by_portfolio(self, portfolio_id: str) -> Iterator[PortfolioMembership]:  # pragma: no cover
        ...


class AllocationStore(ABC):
    @abstractmethod
    def append(self, allocation: CapitalAllocation) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...


class PortfolioDecisionStore(ABC):
    @abstractmethod
    def append(self, decision: PortfolioDecision) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def get_by_id(self, portfolio_decision_id: str) -> PortfolioDecision:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
