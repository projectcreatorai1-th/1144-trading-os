"""Strategy store ports (owned by core.strategy).

Strategies, capability profiles and intents - append-only, restart-safe.
Implementations in platform.database."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from core.strategy.contracts import CapabilityProfile, Strategy, StrategyConfig
from core.strategy.intent import StrategyIntent


class StrategyStore(ABC):
    @abstractmethod
    def save(self, strategy: Strategy) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def get_version(self, strategy_id: str, strategy_version: str) -> Strategy:  # pragma: no cover
        ...

    @abstractmethod
    def iter_versions(self, strategy_id: str) -> Iterator[Strategy]:  # pragma: no cover
        ...

    @abstractmethod
    def iter_all_latest(self) -> Iterator[Strategy]:  # pragma: no cover
        ...

    @abstractmethod
    def save_capability(self, profile: CapabilityProfile) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_capability(self, capability_profile_id: str) -> CapabilityProfile:  # pragma: no cover
        ...

    @abstractmethod
    def save_config(self, config: StrategyConfig) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def get_config(self, strategy_id: str, strategy_version: str) -> StrategyConfig:  # pragma: no cover
        ...


class IntentStore(ABC):
    @abstractmethod
    def append(self, intent: StrategyIntent) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def get_by_id(self, intent_id: str) -> StrategyIntent:  # pragma: no cover
        ...

    @abstractmethod
    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[StrategyIntent]:  # pragma: no cover
        ...

    @abstractmethod
    def count(self) -> int:  # pragma: no cover
        ...
