"""SQLite storage for Phase 4 (owned by platform.database).

Strategies (+capabilities/configs), intents, portfolios, memberships,
allocations and portfolio decisions. Append-only; strategy/portfolio
versions are unique so historical versions can never be overwritten."""
from __future__ import annotations

import json
import sqlite3
from typing import Iterator

from architecture.contracts.errors import StorageError
from architecture.contracts.time import canonical
from core.portfolio.allocation import CapitalAllocation, OverflowBehavior
from core.portfolio.decision import ConflictType, PortfolioConflict, PortfolioDecision
from core.portfolio.exposure import ExposureAggregator, PortfolioExposure
from core.portfolio.portfolio_contract import Portfolio, PortfolioMembership, PortfolioStatus
from core.portfolio.stores import (
    AllocationStore,
    MembershipStore,
    PortfolioDecisionStore,
    PortfolioStore,
)
from core.strategy.contracts import (
    CapabilityProfile,
    Directional,
    Strategy,
    StrategyConfig,
    StrategyLifecycle,
    StrategyType,
    config_hash,
)
from core.strategy.intent import (
    IntentDirection,
    IntentType,
    StrategyIntent,
    Urgency,
)
from core.strategy.stores import IntentStore, StrategyStore

CONTRACT_VERSION = "1.0.0"


def _dump(content: object) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)


class SqliteStrategyStore(StrategyStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def save(self, strategy: Strategy) -> None:
        strategy.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO strategies (strategy_id, strategy_version, lifecycle_status,"
                    " environment, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (strategy.strategy_id, strategy.strategy_version,
                     strategy.lifecycle_status.value, strategy.environment,
                     _dump(strategy.to_dict()), canonical(strategy.created_at),
                     canonical(strategy.updated_at)),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Strategy {strategy.strategy_id}@{strategy.strategy_version} already exists "
                "(versions are immutable)",
                location="sqlite.strategy.save",
            ) from exc

    def get_version(self, strategy_id: str, strategy_version: str) -> Strategy:
        row = self._db.execute(
            "SELECT content FROM strategies WHERE strategy_id = ? AND strategy_version = ?",
            (strategy_id, strategy_version),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Strategy '{strategy_id}@{strategy_version}' not found",
                location="sqlite.strategy.get_version",
            )
        return Strategy.from_storage(json.loads(row[0]))

    def iter_versions(self, strategy_id: str) -> Iterator[Strategy]:
        for row in self._db.execute(
            "SELECT content FROM strategies WHERE strategy_id = ? ORDER BY seq", (strategy_id,)
        ):
            yield Strategy.from_storage(json.loads(row[0]))

    def iter_all_latest(self) -> Iterator[Strategy]:
        for row in self._db.execute(
            "SELECT content FROM strategies WHERE seq IN"
            " (SELECT MAX(seq) FROM strategies GROUP BY strategy_id) ORDER BY seq"
        ):
            yield Strategy.from_storage(json.loads(row[0]))

    def save_capability(self, profile: CapabilityProfile) -> None:
        profile.validate()
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO capability_profiles (capability_profile_id, content)"
                " VALUES (?,?)",
                (profile.capability_profile_id, _dump({
                    "capability_profile_id": profile.capability_profile_id,
                    "strategy_type": profile.strategy_type.value,
                    "supported_symbols": list(profile.supported_symbols),
                    "supported_markets": list(profile.supported_markets),
                    "supported_environments": list(profile.supported_environments),
                    "partial_close_capability": profile.partial_close_capability,
                    "basket_capability": profile.basket_capability,
                    "hedge_capability": profile.hedge_capability,
                    "directional_capability": profile.directional_capability.value,
                    "news_sensitivity": profile.news_sensitivity,
                    "volatility_sensitivity": profile.volatility_sensitivity,
                    "spread_sensitivity": profile.spread_sensitivity,
                    "liquidity_requirement": profile.liquidity_requirement,
                    "max_positions": profile.max_positions,
                    "max_grid_depth": profile.max_grid_depth,
                    "max_observed_lot": profile.max_observed_lot,
                    "max_exposure": profile.max_exposure,
                    "max_risk": profile.max_risk,
                    "recovery_behavior": profile.recovery_behavior,
                    "margin_requirement": profile.margin_requirement,
                    "capacity_estimate": profile.capacity_estimate,
                    "provenance": dict(profile.provenance),
                })),
            )

    def get_capability(self, capability_profile_id: str) -> CapabilityProfile:
        row = self._db.execute(
            "SELECT content FROM capability_profiles WHERE capability_profile_id = ?",
            (capability_profile_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Capability profile '{capability_profile_id}' not found",
                location="sqlite.strategy.get_capability",
            )
        data = json.loads(row[0])
        profile = CapabilityProfile(
            capability_profile_id=data["capability_profile_id"],
            strategy_type=StrategyType(data["strategy_type"]),
            supported_symbols=tuple(data["supported_symbols"]),
            supported_markets=tuple(data["supported_markets"]),
            supported_environments=tuple(data["supported_environments"]),
            partial_close_capability=data["partial_close_capability"],
            basket_capability=data["basket_capability"],
            hedge_capability=data["hedge_capability"],
            directional_capability=Directional(data["directional_capability"]),
            news_sensitivity=data["news_sensitivity"],
            volatility_sensitivity=data["volatility_sensitivity"],
            spread_sensitivity=data["spread_sensitivity"],
            liquidity_requirement=data["liquidity_requirement"],
            max_positions=data.get("max_positions"),
            max_grid_depth=data.get("max_grid_depth"),
            max_observed_lot=data.get("max_observed_lot"),
            max_exposure=data.get("max_exposure"),
            max_risk=data.get("max_risk"),
            recovery_behavior=data.get("recovery_behavior"),
            margin_requirement=data.get("margin_requirement"),
            capacity_estimate=data.get("capacity_estimate"),
            provenance=data.get("provenance", {}),
        )
        profile.validate()
        return profile

    def save_config(self, config: StrategyConfig) -> None:
        config.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO strategy_configs (strategy_id, strategy_version, environment,"
                    " content) VALUES (?,?,?,?)",
                    (config.strategy_id, config.strategy_version, config.environment,
                     _dump({
                         "strategy_id": config.strategy_id,
                         "strategy_version": config.strategy_version,
                         "parameters": dict(config.parameters),
                         "units": dict(config.units),
                         "constraints": {k: list(v) for k, v in config.constraints.items()},
                         "environment": config.environment,
                         "effective_from": canonical(config.effective_from),
                         "effective_to": canonical(config.effective_to)
                         if config.effective_to else None,
                         "provenance": dict(config.provenance),
                         "config_hash": config.config_hash,
                     })),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Config {config.strategy_id}@{config.strategy_version} already stored",
                location="sqlite.strategy.save_config",
            ) from exc

    def get_config(self, strategy_id: str, strategy_version: str) -> StrategyConfig:
        row = self._db.execute(
            "SELECT content FROM strategy_configs WHERE strategy_id = ? AND strategy_version = ?",
            (strategy_id, strategy_version),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Config for '{strategy_id}@{strategy_version}' not found",
                location="sqlite.strategy.get_config",
            )
        from architecture.contracts.time import parse_canonical

        data = json.loads(row[0])
        config = StrategyConfig(
            strategy_id=data["strategy_id"], strategy_version=data["strategy_version"],
            parameters=data["parameters"], units=data["units"],
            constraints={k: tuple(v) for k, v in data["constraints"].items()},
            environment=data["environment"],
            effective_from=parse_canonical(data["effective_from"]),
            effective_to=parse_canonical(data["effective_to"]) if data.get("effective_to") else None,
            provenance=data.get("provenance", {}),
            config_hash=data["config_hash"],
        )
        config.validate()
        return config


class SqliteIntentStore(IntentStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, intent: StrategyIntent) -> None:
        intent.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO strategy_intents (intent_id, strategy_id, environment,"
                    " intent_type, symbol, created_at, correlation_id, content)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (intent.intent_id, intent.strategy_id, intent.environment,
                     intent.intent_type.value, intent.symbol,
                     canonical(intent.created_at), intent.correlation_id,
                     _dump(intent.to_dict())),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Intent {intent.intent_id} already stored (canonical intents are unique)",
                location="sqlite.intent.append",
            ) from exc

    def get_by_id(self, intent_id: str) -> StrategyIntent:
        row = self._db.execute(
            "SELECT content FROM strategy_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Intent '{intent_id}' not found", location="sqlite.intent.get_by_id",
            )
        return self._to_intent(json.loads(row[0]))

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[StrategyIntent]:
        for row in self._db.execute(
            "SELECT content FROM strategy_intents WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self._to_intent(json.loads(row[0]))

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM strategy_intents").fetchone()[0])

    @staticmethod
    def _to_intent(data: dict) -> StrategyIntent:
        from architecture.contracts.time import parse_canonical

        intent = StrategyIntent(
            intent_id=data["intent_id"], strategy_id=data["strategy_id"],
            strategy_version=data["strategy_version"],
            intent_type=IntentType(data["intent_type"]), symbol=data["symbol"],
            direction=IntentDirection(data["direction"]),
            requested_quantity=data["requested_quantity"],
            requested_notional=data.get("requested_notional"),
            entry_conditions=tuple(data["entry_conditions"]),
            exit_conditions=tuple(data["exit_conditions"]),
            urgency=Urgency(data["urgency"]), confidence=data.get("confidence"),
            rationale=data["rationale"], policy_id=data.get("policy_id"),
            policy_version=data.get("policy_version"),
            risk_context_hash=data["risk_context_hash"],
            source_event_id=data["source_event_id"],
            correlation_id=data["correlation_id"],
            causation_id=data.get("causation_id"), environment=data["environment"],
            created_at=parse_canonical(data["created_at"]),
            expires_at=parse_canonical(data["expires_at"]),
        )
        intent.validate()
        return intent


class SqlitePortfolioStore(PortfolioStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def save(self, portfolio: Portfolio) -> None:
        portfolio.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO portfolios (portfolio_id, portfolio_version, status,"
                    " environment, content) VALUES (?,?,?,?,?)",
                    (portfolio.portfolio_id, portfolio.portfolio_version,
                     portfolio.status.value, portfolio.environment,
                     _dump(portfolio.to_dict())),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Portfolio {portfolio.portfolio_id}@{portfolio.portfolio_version} already exists",
                location="sqlite.portfolio.save",
            ) from exc

    def get_version(self, portfolio_id: str, portfolio_version: str) -> Portfolio:
        row = self._db.execute(
            "SELECT content FROM portfolios WHERE portfolio_id = ? AND portfolio_version = ?",
            (portfolio_id, portfolio_version),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Portfolio '{portfolio_id}@{portfolio_version}' not found",
                location="sqlite.portfolio.get_version",
            )
        return Portfolio.from_storage(json.loads(row[0]))

    def iter_all_latest(self) -> Iterator[Portfolio]:
        for row in self._db.execute(
            "SELECT content FROM portfolios WHERE seq IN"
            " (SELECT MAX(seq) FROM portfolios GROUP BY portfolio_id) ORDER BY seq"
        ):
            yield Portfolio.from_storage(json.loads(row[0]))


class SqliteMembershipStore(MembershipStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, membership: PortfolioMembership) -> None:
        membership.validate()
        with self._db:
            self._db.execute(
                "INSERT INTO portfolio_memberships (portfolio_id, strategy_id, priority,"
                " enabled, effective_from, content) VALUES (?,?,?,?,?,?)",
                (membership.portfolio_id, membership.strategy_id, membership.priority,
                 int(membership.enabled), canonical(membership.effective_from),
                 _dump(membership.to_dict())),
            )

    def iter_by_portfolio(self, portfolio_id: str) -> Iterator[PortfolioMembership]:
        from architecture.contracts.time import parse_canonical

        for row in self._db.execute(
            "SELECT content FROM portfolio_memberships WHERE portfolio_id = ? ORDER BY seq",
            (portfolio_id,),
        ):
            data = json.loads(row[0])
            membership = PortfolioMembership(
                portfolio_id=data["portfolio_id"], strategy_id=data["strategy_id"],
                strategy_version=data["strategy_version"], allocation=data["allocation"],
                risk_budget=data["risk_budget"], priority=int(data["priority"]),
                enabled=bool(data["enabled"]),
                effective_from=parse_canonical(data["effective_from"]),
                effective_to=parse_canonical(data["effective_to"]) if data.get("effective_to") else None,
                environment=data["environment"],
            )
            membership.validate()
            yield membership


class SqliteAllocationStore(AllocationStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, allocation: CapitalAllocation) -> None:
        allocation.validate()
        with self._db:
            self._db.execute(
                "INSERT INTO capital_allocations (capital_allocation_id, portfolio_id, content)"
                " VALUES (?,?,?)",
                (allocation.capital_allocation_id, allocation.portfolio_id,
                 _dump({
                     "capital_allocation_id": allocation.capital_allocation_id,
                     "portfolio_id": allocation.portfolio_id,
                     "portfolio_version": allocation.portfolio_version,
                     "total_capital": allocation.total_capital,
                     "reserved_capital": allocation.reserved_capital,
                     "strategy_allocations": dict(allocation.strategy_allocations),
                     "symbol_allocations": dict(allocation.symbol_allocations),
                     "risk_allocations": dict(allocation.risk_allocations),
                     "available_capital": allocation.available_capital,
                     "remaining_capital": allocation.remaining_capital,
                     "allocated_capital": allocation.allocated_capital,
                     "environment": allocation.environment,
                     "computed_at": allocation.computed_at,
                 })),
            )

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM capital_allocations").fetchone()[0])


class SqlitePortfolioDecisionStore(PortfolioDecisionStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, decision: PortfolioDecision) -> None:
        decision.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO portfolio_decisions (portfolio_decision_id, portfolio_id,"
                    " correlation_id, content) VALUES (?,?,?,?)",
                    (decision.portfolio_decision_id, decision.portfolio_id,
                     decision.correlation_id, _dump(self._to_content(decision))),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Portfolio decision {decision.portfolio_decision_id} already stored "
                "(canonical decisions are unique)",
                location="sqlite.pdecision.append",
            ) from exc

    def get_by_id(self, portfolio_decision_id: str) -> PortfolioDecision:
        row = self._db.execute(
            "SELECT content FROM portfolio_decisions WHERE portfolio_decision_id = ?",
            (portfolio_decision_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Portfolio decision '{portfolio_decision_id}' not found",
                location="sqlite.pdecision.get_by_id",
            )
        return self._from_content(json.loads(row[0]))

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM portfolio_decisions").fetchone()[0])

    @staticmethod
    def _to_content(decision: PortfolioDecision) -> dict:
        return {
            "portfolio_decision_id": decision.portfolio_decision_id,
            "portfolio_id": decision.portfolio_id,
            "portfolio_version": decision.portfolio_version,
            "environment": decision.environment,
            "active_strategies": list(decision.active_strategies),
            "eligible_strategies": list(decision.eligible_strategies),
            "allocations": dict(decision.allocations),
            "risk_budgets": dict(decision.risk_budgets),
            "projected_exposure": dict(decision.projected_exposure),
            "triggered_constraints": list(decision.triggered_constraints),
            "exceeded_capacities": list(decision.exceeded_capacities),
            "liquidity_status": dict(decision.liquidity_status),
            "conflicts": [
                {"conflict_type": c.conflict_type.value, "strategies": list(c.strategies),
                 "detail": c.detail} for c in decision.conflicts],
            "resulting_state": dict(decision.resulting_state),
            "risk_decision_id": decision.risk_decision_id,
            "created_at": decision.created_at.isoformat(),
            "correlation_id": decision.correlation_id,
        }

    @staticmethod
    def _from_content(data: dict) -> PortfolioDecision:
        from datetime import datetime as dt

        decision = PortfolioDecision(
            portfolio_decision_id=data["portfolio_decision_id"],
            portfolio_id=data["portfolio_id"],
            portfolio_version=data["portfolio_version"],
            environment=data["environment"],
            active_strategies=tuple(data["active_strategies"]),
            eligible_strategies=tuple(data["eligible_strategies"]),
            allocations=data["allocations"], risk_budgets=data["risk_budgets"],
            projected_exposure=data["projected_exposure"],
            triggered_constraints=tuple(data["triggered_constraints"]),
            exceeded_capacities=tuple(data["exceeded_capacities"]),
            liquidity_status=data["liquidity_status"],
            conflicts=tuple(PortfolioConflict(
                ConflictType(c["conflict_type"]), tuple(c["strategies"]), c["detail"])
                for c in data["conflicts"]),
            resulting_state=data["resulting_state"],
            created_at=dt.fromisoformat(data["created_at"]),
            correlation_id=data["correlation_id"],
            risk_decision_id=data.get("risk_decision_id"),
        )
        decision.validate()
        return decision
