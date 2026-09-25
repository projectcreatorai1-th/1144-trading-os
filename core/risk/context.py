"""Canonical RiskContext (owned by core.risk).

Point-in-time snapshot of everything risk evaluation needs. Missing values
are None (= UNKNOWN, never safe); monetary values are canonical decimal
strings. The context hash makes decisions reproducible (SECTION 8/22)."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"

DECIMAL_FIELDS = (
    "account_balance", "account_equity", "account_free_margin", "account_used_margin",
    "account_margin_level", "account_available_capital",
    "long_exposure", "short_exposure", "net_exposure", "gross_exposure",
    "current_risk_pct", "projected_risk_pct", "daily_loss", "daily_loss_pct",
    "drawdown", "drawdown_pct", "peak_equity",
)

MARKET_STATES = ("CALM", "NORMAL", "VOLATILE", "EXTREME", "UNKNOWN")
EVENT_RISK_LEVELS = ("NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN")
DATA_QUALITY_LEVELS = ("VERIFIED", "VALIDATED", "DEGRADED", "STALE", "INVALID", "UNKNOWN")


@dataclass(frozen=True)
class RiskContext:
    context_id: str
    context_hash: str
    as_of: datetime
    environment: str
    account_balance: str | None = None
    account_equity: str | None = None
    account_free_margin: str | None = None
    account_used_margin: str | None = None
    account_margin_level: str | None = None
    account_available_capital: str | None = None
    position_count: int | None = None
    long_exposure: str | None = None
    short_exposure: str | None = None
    net_exposure: str | None = None
    gross_exposure: str | None = None
    symbol_exposure: Mapping[str, str] | None = None
    strategy_exposure: Mapping[str, str] | None = None
    current_risk_pct: str | None = None
    projected_risk_pct: str | None = None
    daily_loss: str | None = None
    daily_loss_pct: str | None = None
    drawdown: str | None = None
    drawdown_pct: str | None = None
    peak_equity: str | None = None
    recovery_state: str | None = None
    market_state: str | None = None
    volatility_state: str | None = None
    spread_state: str | None = None
    liquidity_state: str | None = None
    data_quality: str | None = None
    data_stale: bool | None = None
    data_missing: bool | None = None
    data_invalid: bool | None = None
    data_unknown: bool | None = None
    system_state: str | None = None
    execution_state: str | None = None
    active_events: tuple[str, ...] | None = None
    event_risk: str | None = None
    event_window: str | None = None
    event_severity: str | None = None

    def validate(self) -> None:
        validate_identifier("risk_context_id", self.context_id, location="context.context_id")
        if not isinstance(self.context_hash, str) or len(self.context_hash) != 64:
            raise ContractValidationError(
                "context.context_hash must be a sha-256 hex string",
                location="context.context_hash",
            )
        ensure_utc(self.as_of, location="context.as_of")
        parse_environment(self.environment, location="context.environment")
        for name in DECIMAL_FIELDS:
            value = getattr(self, name)
            if value is not None:
                parse_decimal(value, location=f"context.{name}")  # floats rejected here
        if self.position_count is not None and (not isinstance(self.position_count, int) or isinstance(self.position_count, bool) or self.position_count < 0):
            raise ContractValidationError(
                "context.position_count must be a non-negative integer",
                location="context.position_count",
            )
        if self.market_state is not None and self.market_state not in MARKET_STATES:
            raise ContractValidationError(
                f"context.market_state must be one of {MARKET_STATES}",
                location="context.market_state",
                rule_id="SCHEMA-ENUM",
            )
        if self.event_risk is not None and self.event_risk not in EVENT_RISK_LEVELS:
            raise ContractValidationError(
                f"context.event_risk must be one of {EVENT_RISK_LEVELS}",
                location="context.event_risk",
                rule_id="SCHEMA-ENUM",
            )
        if self.data_quality is not None and self.data_quality not in DATA_QUALITY_LEVELS:
            raise ContractValidationError(
                f"context.data_quality must be one of {DATA_QUALITY_LEVELS}",
                location="context.data_quality",
                rule_id="SCHEMA-ENUM",
            )
        for name in ("data_stale", "data_missing", "data_invalid", "data_unknown"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ContractValidationError(
                    f"context.{name} must be a boolean or None",
                    location=f"context.{name}",
                )
        for name in ("symbol_exposure", "strategy_exposure"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise ContractValidationError(
                        f"context.{name} must be a mapping of decimal strings",
                        location=f"context.{name}",
                    )
                for key, item in value.items():
                    parse_decimal(item, location=f"context.{name}.{key}")

    def to_content(self) -> dict[str, Any]:
        """Canonical, JSON-safe content for hashing and storage (excludes
        context_id/hash themselves; datetimes as canonical ISO strings)."""
        return self.to_content_values(serialize_datetimes=True)

    def to_content_values(self, serialize_datetimes: bool = False) -> dict[str, Any]:
        """Field values; datetimes stay datetimes unless serialized - used
        for re-projecting contexts (Phase 4 intent gate)."""
        from architecture.contracts.time import canonical as _canonical

        def _safe(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {k: _safe(v) for k, v in value.items()}
            if isinstance(value, tuple):
                return [_safe(v) for v in value]
            if isinstance(value, datetime) and serialize_datetimes:
                return _canonical(value)
            return value

        return {
            name: _safe(getattr(self, name))
            for name in (
                "as_of", "environment", "account_balance", "account_equity",
                "account_free_margin", "account_used_margin", "account_margin_level",
                "account_available_capital", "position_count", "long_exposure",
                "short_exposure", "net_exposure", "gross_exposure", "symbol_exposure",
                "strategy_exposure", "current_risk_pct", "projected_risk_pct",
                "daily_loss", "daily_loss_pct", "drawdown", "drawdown_pct",
                "peak_equity", "recovery_state", "market_state", "volatility_state",
                "spread_state", "liquidity_state", "data_quality", "data_stale",
                "data_missing", "data_invalid", "data_unknown", "system_state",
                "execution_state", "active_events", "event_risk", "event_window",
                "event_severity",
            )
        }

    def flattened(self) -> dict[str, Any]:
        """Dotted-path view for policy rule resolution."""
        return {
            "account": {
                "balance": self.account_balance, "equity": self.account_equity,
                "free_margin": self.account_free_margin, "used_margin": self.account_used_margin,
                "margin_level": self.account_margin_level,
                "available_capital": self.account_available_capital,
            },
            "positions": {
                "count": self.position_count, "long": self.long_exposure,
                "short": self.short_exposure, "net": self.net_exposure,
                "gross": self.gross_exposure,
                "by_symbol": dict(self.symbol_exposure or {}),
                "by_strategy": dict(self.strategy_exposure or {}),
            },
            "risk": {
                "current_pct": self.current_risk_pct, "projected_pct": self.projected_risk_pct,
                "daily_loss": self.daily_loss, "daily_loss_pct": self.daily_loss_pct,
                "drawdown": self.drawdown, "drawdown_pct": self.drawdown_pct,
                "peak_equity": self.peak_equity, "recovery_state": self.recovery_state,
            },
            "market": {
                "state": self.market_state, "volatility": self.volatility_state,
                "spread": self.spread_state, "liquidity": self.liquidity_state,
            },
            "data": {
                "quality": self.data_quality, "stale": self.data_stale,
                "missing": self.data_missing, "invalid": self.data_invalid,
                "unknown": self.data_unknown,
            },
            "system": {
                "state": self.system_state, "execution_state": self.execution_state,
                "environment": self.environment,
            },
            "event": {
                "active": list(self.active_events or ()), "risk": self.event_risk,
                "window": self.event_window, "severity": self.event_severity,
            },
        }

    def resolve(self, dotted: str) -> tuple[bool, Any]:
        node: Any = self.flattened()
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return False, None
            node = node[part]
        return True, node


def build_context(*, as_of: datetime, environment: str, **values: Any) -> RiskContext:
    from core.policy.evaluation import canonical_hash

    context = RiskContext(
        context_id=new_identifier("risk_context_id"),
        context_hash="0" * 64,  # replaced below
        as_of=as_of,
        environment=environment,
        **values,
    )
    object.__setattr__(context, "context_hash", canonical_hash(context.to_content()))
    context.validate()
    return context
