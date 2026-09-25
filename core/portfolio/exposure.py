"""Portfolio exposure aggregation (owned by core.portfolio).

Centralized aggregation across strategies: gross/net/long/short, per symbol,
per strategy, per direction, per market, correlated groups (UNKNOWN without
correlation data). Per-strategy risk isolation is forbidden (SECTION 22/23)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import validate_identifier
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"

UNKNOWN_GROUPS = {"UNKNOWN": ["correlation data unavailable"]}


@dataclass(frozen=True)
class ExposureLeg:
    """One position/exposure contribution from a strategy.

    exposure is an unsigned magnitude; direction carries the sign
    (LONG positive, SHORT negative) - the aggregator applies signs centrally."""

    strategy_id: str
    symbol: str
    market: str
    direction: str  # LONG / SHORT / FLAT
    exposure: str   # canonical decimal magnitude (>= 0)


@dataclass(frozen=True)
class PortfolioExposure:
    portfolio_id: str
    gross_exposure: str
    net_exposure: str
    long_exposure: str
    short_exposure: str
    by_symbol: Mapping[str, str]
    by_strategy: Mapping[str, str]
    by_direction: Mapping[str, str]
    by_market: Mapping[str, str]
    correlation_groups: Mapping[str, Any]
    computed_at: datetime
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("portfolio_id", self.portfolio_id, location="exposure.portfolio_id")
        for name in ("gross_exposure", "net_exposure", "long_exposure", "short_exposure"):
            parse_decimal(getattr(self, name), location=f"exposure.{name}")
        for group in ("by_symbol", "by_strategy", "by_direction", "by_market"):
            value = getattr(self, group)
            if not isinstance(value, Mapping):
                raise ContractValidationError(
                    f"exposure.{group} must be a mapping", location=f"exposure.{group}",
                )
            for key, item in value.items():
                parse_decimal(item, location=f"exposure.{group}.{key}")
        # centralized arithmetic: gross = |long| + |short|; net = long + short (short <= 0)
        gross = parse_decimal(self.gross_exposure, location="exposure.gross")
        long_ = parse_decimal(self.long_exposure, location="exposure.long")
        short = parse_decimal(self.short_exposure, location="exposure.short")
        net = parse_decimal(self.net_exposure, location="exposure.net")
        if gross != abs(long_) + abs(short):
            raise ContractValidationError(
                "gross != |long| + |short| (aggregation arithmetic violated)",
                location="exposure.arithmetic", rule_id="EXPOSURE-001",
            )
        if net != long_ + short:
            raise ContractValidationError(
                "net != long + short", location="exposure.arithmetic", rule_id="EXPOSURE-001",
            )
        ensure_utc(self.computed_at, location="exposure.computed_at")


class ExposureAggregator:
    """Deterministic aggregation over exposure legs."""

    def aggregate(
        self, *, portfolio_id: str, legs: Iterable[ExposureLeg], environment: str,
        computed_at: datetime, correlation_groups: Mapping[str, list[str]] | None = None,
    ) -> PortfolioExposure:
        long_total = Decimal("0")
        short_total = Decimal("0")
        by_symbol: dict[str, Decimal] = {}
        by_strategy: dict[str, Decimal] = {}
        by_direction: dict[str, Decimal] = {"LONG": Decimal("0"), "SHORT": Decimal("0")}
        by_market: dict[str, Decimal] = {}
        for leg in legs:
            magnitude = parse_decimal(leg.exposure, location="exposure.leg")
            if magnitude < 0:
                raise ContractValidationError(
                    f"Negative exposure magnitude from {leg.strategy_id} "
                    "(direction carries the sign)",
                    location="exposure.leg", rule_id="EXPOSURE-001",
                )
            value = magnitude if leg.direction == "LONG" else -magnitude if leg.direction == "SHORT" else Decimal("0")
            if leg.direction == "LONG":
                long_total += magnitude
            elif leg.direction == "SHORT":
                short_total -= magnitude
            by_symbol[leg.symbol] = by_symbol.get(leg.symbol, Decimal("0")) + value
            by_strategy[leg.strategy_id] = by_strategy.get(leg.strategy_id, Decimal("0")) + value
            by_direction[leg.direction] = by_direction.get(leg.direction, Decimal("0")) + value
            by_market[leg.market] = by_market.get(leg.market, Decimal("0")) + value
        exposure = PortfolioExposure(
            portfolio_id=portfolio_id,
            gross_exposure=str(abs(long_total) + abs(short_total)),
            net_exposure=str(long_total + short_total),
            long_exposure=str(long_total),
            short_exposure=str(short_total),
            by_symbol={k: str(v) for k, v in sorted(by_symbol.items())},
            by_strategy={k: str(v) for k, v in sorted(by_strategy.items())},
            by_direction={k: str(v) for k, v in sorted(by_direction.items())},
            by_market={k: str(v) for k, v in sorted(by_market.items())},
            correlation_groups=dict(correlation_groups) if correlation_groups else dict(UNKNOWN_GROUPS),
            computed_at=ensure_utc(computed_at, location="exposure.computed_at"),
            environment=environment,
        )
        exposure.validate()
        return exposure

    @staticmethod
    def correlated_exposure(exposure: PortfolioExposure,
                            group_symbols: list[str]) -> str:
        """Combined exposure of a correlation group; UNKNOWN groups carry no number."""
        total = Decimal("0")
        for symbol in group_symbols:
            if symbol not in exposure.by_symbol:
                continue
            total += abs(parse_decimal(exposure.by_symbol[symbol], location="exposure.group"))
        return str(total)
