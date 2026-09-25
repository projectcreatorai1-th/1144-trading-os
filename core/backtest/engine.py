"""Backtest engine (owned by core.backtest).

Deterministic historical simulation reusing production semantics. The clock
walks historical event time only (SECTION 27); wall-clock access is
impossible by construction. Backtest orders NEVER shortcut
Strategy -> Portfolio -> Risk (SECTION 30)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal
from core.research.contracts import (
    ExecutionModel,
    Observation,
    ResearchConfig,
    ResearchDataset,
)

CONTRACT_VERSION = "1.0.0"


@dataclass
class SimulationClock:
    """Deterministic clock walking historical time; cannot go backwards and
    exposes no wall clock."""
    current: datetime

    @classmethod
    def start(cls, moment: datetime) -> "SimulationClock":
        return cls(current=ensure_utc(moment, location="clock.start"))

    def advance_to(self, moment: datetime) -> None:
        moment = ensure_utc(moment, location="clock.advance")
        if moment < self.current:
            raise ContractError(
                f"Simulation clock cannot move backwards "
                f"({self.current.isoformat()} -> {moment.isoformat()})",
                location="clock.advance",
            )
        self.current = moment

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True)
class SimulatedTrade:
    trade_id: str
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    entry_time: datetime
    exit_time: datetime | None
    pnl: Decimal
    fees: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id, "symbol": self.symbol, "side": self.side,
            "quantity": str(self.quantity),
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price) if self.exit_price is not None else None,
            "entry_time": ensure_utc(self.entry_time).isoformat(),
            "exit_time": ensure_utc(self.exit_time).isoformat() if self.exit_time else None,
            "pnl": str(self.pnl), "fees": str(self.fees),
        }


@dataclass
class BacktestState:
    cash: Decimal
    position_quantity: Decimal = Decimal("0")
    position_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    equity_high: Decimal = Decimal("0")
    equity_low: Decimal = Decimal("0")
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    trades: list[SimulatedTrade] = field(default_factory=list)
    decisions: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class StrategyLogic:
    """Research strategy interface (SECTION 23): data-in, action-out. Logic
    NEVER calls brokers/MT5, NEVER modifies risk/policy/OMS/ledger."""

    strategy_id: str
    strategy_version: str
    logic_hash: str
    on_observation: Callable[[Observation, BacktestState], str | None]

    def validate(self) -> None:
        validate_identifier("strategy_id", self.strategy_id, location="logic.strategy_id")
        if len(self.logic_hash) != 64:
            raise ContractValidationError(
                "logic.logic_hash must be a sha-256 hex string",
                location="logic.logic_hash")
        if not callable(self.on_observation):
            raise ContractValidationError(
                "logic.on_observation must be callable",
                location="logic.on_observation")


def explicit_cost(value: str, location: str) -> Decimal:
    """SECTION 115: no silent zero fallback - UNKNOWN costs fail closed."""
    if value == "UNKNOWN":
        raise ContractError(
            f"{location} is UNKNOWN - research cannot run with unknown costs "
            "(fail closed; declare an explicit assumption)",
            location=location, rule_id="NOFALLBACK-001",
        )
    return parse_decimal(value, location=location)


def observation_price(observation: Observation) -> Decimal:
    for key in ("price", "close", "mid", "value"):
        if key in observation.payload:
            return parse_decimal(observation.payload[key], location=f"obs.{key}")
    raise ContractError(
        f"Observation for {observation.symbol} carries no price field",
        location="bt.observation", rule_id="DATA-001")


@dataclass(frozen=True)
class BacktestOutcome:
    state: BacktestState
    metrics: dict[str, Any]
    final_equity: Decimal
    clock_end: datetime


class BacktestEngine:
    """Canonical pipeline: dataset -> PIT view -> strategy action -> policy
    gate -> deterministic simulated fill -> position -> equity/metrics."""

    def __init__(self, *, config: ResearchConfig, model: ExecutionModel,
                 policy_allows: Callable[[str], bool] | None = None) -> None:
        config.validate()
        model.validate()
        self._config = config
        self._model = model
        self._policy_allows = policy_allows or (lambda action: True)

    def run(self, *, dataset: ResearchDataset, logic: StrategyLogic,
            decision_sink: list | None = None) -> BacktestOutcome:
        dataset.validate()
        logic.validate()
        clock = SimulationClock.start(datetime.fromisoformat(dataset.time_range["start"]))
        state = BacktestState(cash=parse_decimal(self._config.initial_capital,
                                                 location="bt.capital"))
        state.equity_high = state.cash
        state.equity_low = state.cash
        spread = explicit_cost(self._config.spread, "config.spread")
        slippage = explicit_cost(self._config.slippage, "config.slippage")
        commission = explicit_cost(self._config.commission, "config.commission")

        for observation in dataset.observations:
            clock.advance_to(ensure_utc(observation.available_time))
            consumed = tuple(
                index for index, other in enumerate(dataset.observations)
                if ensure_utc(other.available_time) <= clock.now()
                and other.symbol == observation.symbol
            )
            from core.research.bias import DecisionRecord

            record = DecisionRecord(clock.now(), consumed)
            if decision_sink is not None:
                decision_sink.append(record)
            state.decisions.append(record)

            action = logic.on_observation(observation, state)
            if action is None:
                self._mark_equity(state, observation, clock.now())
                continue
            action = str(action).upper()
            if not self._policy_allows(action):
                self._mark_equity(state, observation, clock.now())
                continue  # policy gate fail-closed: no simulated order
            self._apply_fill(state, observation, action, slippage, commission, clock.now())
            self._mark_equity(state, observation, clock.now())

        last = observation_price(dataset.observations[-1]) if dataset.observations else Decimal("0")
        return BacktestOutcome(
            state=state,
            metrics=self._metrics(state),
            final_equity=self._equity(state, last),
            clock_end=clock.now(),
        )

    # ------------------------------------------------------------------ #
    def _apply_fill(self, state: BacktestState, observation: Observation,
                    action: str, slippage: Decimal, commission: Decimal,
                    moment: datetime) -> None:
        price = observation_price(observation)
        fill = price + slippage if action in ("OPEN", "BUY", "LONG") else price - slippage
        quantity = Decimal("1")
        if action in ("OPEN", "BUY", "LONG") and state.position_quantity == 0:
            state.position_quantity = quantity
            state.position_price = fill
            state.fees_paid += commission
            state.cash -= commission
        elif action in ("CLOSE", "SELL", "EXIT") and state.position_quantity > 0:
            pnl = (fill - state.position_price) * state.position_quantity
            state.realized_pnl += pnl
            state.cash += pnl - commission
            state.fees_paid += commission
            state.trades.append(SimulatedTrade(
                trade_id=new_identifier("request_id"),
                symbol=observation.symbol, side="LONG",
                quantity=state.position_quantity,
                entry_price=state.position_price, exit_price=fill,
                entry_time=moment, exit_time=moment,
                pnl=pnl, fees=commission))
            state.position_quantity = Decimal("0")
            state.position_price = Decimal("0")

    def _mark_equity(self, state: BacktestState, observation: Observation,
                     moment: datetime) -> None:
        price = observation_price(observation)
        equity = self._equity(state, price)
        state.equity_high = max(state.equity_high, equity)
        state.equity_low = min(state.equity_low, equity)
        state.equity_curve.append({"time": moment.isoformat(), "equity": str(equity)})

    def _equity(self, state: BacktestState, price: Decimal) -> Decimal:
        unrealized = (price - state.position_price) * state.position_quantity \
            if state.position_quantity > 0 else Decimal("0")
        return state.cash + unrealized

    @staticmethod
    def _metrics(state: BacktestState) -> dict[str, Any]:
        wins = [t for t in state.trades if t.pnl > 0]
        losses = [t for t in state.trades if t.pnl <= 0]
        gross_profit = sum((t.pnl for t in wins), Decimal("0"))
        gross_loss = abs(sum((t.pnl for t in losses), Decimal("0")))
        net_pnl = state.realized_pnl - state.fees_paid
        drawdown = state.equity_high - state.equity_low
        win_rate = Decimal(len(wins)) / Decimal(len(state.trades)) if state.trades else Decimal("0")
        profit_factor = (
            Decimal("Infinity") if gross_loss == 0 and gross_profit > 0
            else gross_profit / gross_loss if gross_loss > 0 else Decimal("0")
        )
        expectancy = net_pnl / Decimal(len(state.trades)) if state.trades else Decimal("0")
        return {
            "net_pnl": str(net_pnl),
            "gross_profit": str(gross_profit),
            "gross_loss": str(gross_loss),
            "trade_count": len(state.trades),
            "win_rate": str(win_rate),
            "profit_factor": str(profit_factor),
            "expectancy": str(expectancy),
            "max_drawdown": str(drawdown),
            "fees_paid": str(state.fees_paid),
        }
