"""Controlled, deterministic contract instances for tests.

TEST-SCOPE ONLY (SECTION 29): this module lives under tests/ and is never
imported by production code. All timestamps are fixed and timezone-aware;
all identifiers follow the global identifier standard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from adapters.ai.contracts import AIAnalysis, AIAnalysisType, Model, ModelVersion
from architecture.contracts.config import ConfigContract, ConfigKind
from architecture.contracts.environment import EnvironmentDeclaration
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.provenance import Provenance
from core.decision.contracts import Decision, DecisionStatus, DecisionType
from core.events.contracts import Event, EventType
from core.execution.contracts import Order, OrderSide, OrderStatus, OrderType, TimeInForce
from core.ledger.contracts import LedgerEntry, LedgerType
from core.policy.contracts import Policy, PolicyStatus, PolicyType
from core.portfolio.contracts import Position, PositionSide, PositionStatus
from core.risk.contracts import MarketState, RiskDecision, RiskResult, RiskState
from core.validation.contracts import DataQualityLevel, DataQualityReport
from platform.api.contracts import APIRequest, APIResponse
from platform.audit.contracts import ActorType, AuditRecord
from platform.security.contracts import Permission, PermissionGrant, Role

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def at(hour: int, minute: int = 0) -> datetime:
    return T0.replace(hour=hour, minute=minute)


def make_provenance(**overrides):
    defaults = dict(
        source="adapters.market_data",
        source_id=new_identifier("event_id"),
        ingestion_time=at(12, 0),
        event_time=at(11, 59),
        processing_time=at(12, 0),
        model_version="analysis-1.0.0",
        policy_version="1.0.0",
        data_version="2026-09-23.1",
    )
    defaults.update(overrides)
    return Provenance(**defaults)


def make_data_quality(level: DataQualityLevel = DataQualityLevel.VERIFIED, **overrides):
    defaults = dict(
        level=level,
        checked_at=at(12, 0),
        missing_fields=(),
        stale_data=False,
        details={"feed": "ok"},
    )
    defaults.update(overrides)
    return DataQualityReport(**defaults)


def make_event(**overrides):
    defaults = dict(
        event_id=new_identifier("event_id"),
        event_type=EventType.MARKET_DATA_RECEIVED,
        event_version="1.0.0",
        event_time=at(12, 0),
        received_time=at(12, 0, ),
        source="adapters.market_data",
        source_id="feed-xauusd",
        environment="PAPER",
        correlation_id=new_identifier("correlation_id"),
        payload={"symbol": "XAUUSD", "bid": "2650.10", "ask": "2650.30"},
    )
    defaults.update(overrides)
    return Event(**defaults)


def make_decision(**overrides):
    defaults = dict(
        decision_id=new_identifier("decision_id"),
        decision_type=DecisionType.ENTRY,
        status=DecisionStatus.PROPOSED,
        environment="PAPER",
        scope="XAUUSD",
        strategy_id=new_identifier("strategy_id"),
        source="core.strategy.grid-alpha",
        confidence=0.72,
        reasons=("grid level 3 reached", "volatility in band"),
        output={"action": "BUY", "quantity": "0.10", "symbol": "XAUUSD"},
        provenance=make_provenance(),
        correlation_id=new_identifier("correlation_id"),
        decision_time=at(12, 1),
    )
    defaults.update(overrides)
    return Decision(**defaults)


def make_policy(**overrides):
    defaults = dict(
        policy_id=new_identifier("policy_id"),
        policy_version="1.2.0",
        policy_type=PolicyType.GRID,
        status=PolicyStatus.DRAFT,
        scope="XAUUSD",
        conditions=({"when": "volatility_band", "op": "<=", "value": "2.0"},),
        actions=({"allow": "grid_entry"},),
        limits={"max_levels": 5, "max_exposure": "10000"},
        priority=10,
        effective_from=at(0, 0),
        created_by=new_identifier("user_id"),
        created_at=at(10, 0),
        updated_at=at(10, 0),
    )
    defaults.update(overrides)
    return Policy(**defaults)


def make_risk_decision(
    decision: RiskResult = RiskResult.ALLOW,
    market_state: MarketState = MarketState.CALM,
    data_quality: DataQualityLevel = DataQualityLevel.VERIFIED,
    environment: str = "PAPER",
    **overrides,
):
    defaults = dict(
        risk_decision_id=new_identifier("risk_decision_id"),
        environment=environment,
        scope="XAUUSD",
        decision=decision,
        reasons=("exposure within limit",),
        limits={"max_exposure": "10000"},
        current_exposure=4000.0,
        requested_exposure=1000.0,
        risk_metrics={"var": "120.5", "drawdown": "0.8"},
        risk_state=RiskState.NORMAL,
        market_state=market_state,
        data_quality=data_quality,
        confidence=0.9,
        policy_reference=f"{new_identifier('policy_id')}@1.2.0",
        correlation_id=new_identifier("correlation_id"),
        decision_time=at(12, 2),
        expires_at=at(13, 0),
    )
    defaults.update(overrides)
    return RiskDecision(**defaults)


def make_order(status: OrderStatus = OrderStatus.RISK_CHECK, **overrides):
    defaults = dict(
        order_id=new_identifier("order_id"),
        client_order_id="EA-GRID-000123",
        strategy_id=new_identifier("strategy_id"),
        symbol="XAUUSD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.10"),
        time_in_force=TimeInForce.GTC,
        environment="PAPER",
        policy_id=new_identifier("policy_id"),
        status=status,
        source="core.strategy.grid-alpha",
        created_at=at(12, 3),
        updated_at=at(12, 3),
        correlation_id=new_identifier("correlation_id"),
    )
    defaults.update(overrides)
    return Order(**defaults)


def make_position(**overrides):
    defaults = dict(
        position_id=new_identifier("position_id"),
        account_id="ACC-1144-DEMO",
        symbol="XAUUSD",
        side=PositionSide.LONG,
        quantity=Decimal("0.10"),
        average_price=Decimal("2650.20"),
        unrealized_pnl=Decimal("2.50"),
        realized_pnl=Decimal("0"),
        margin=Decimal("265.02"),
        exposure=Decimal("265.02"),
        strategy_id=new_identifier("strategy_id"),
        environment="PAPER",
        opened_at=at(12, 5),
        updated_at=at(12, 5),
        status=PositionStatus.OPEN,
        correlation_id=new_identifier("correlation_id"),
    )
    defaults.update(overrides)
    return Position(**defaults)


def make_ledger_entry(**overrides):
    defaults = dict(
        ledger_entry_id=new_identifier("ledger_entry_id"),
        entry_type=LedgerType.EXECUTION,
        account_id="ACC-1144-DEMO",
        amount=Decimal("-2.40"),
        currency="USD",
        environment="PAPER",
        entry_time=at(12, 6),
        correlation_id=new_identifier("correlation_id"),
        order_id=new_identifier("order_id"),
    )
    defaults.update(overrides)
    return LedgerEntry(**defaults)


def make_audit_record(**overrides):
    defaults = dict(
        audit_id=new_identifier("audit_id"),
        actor_type=ActorType.SYSTEM,
        actor_id="core.execution",
        action="ORDER_SUBMITTED",
        entity_type="order",
        entity_id=new_identifier("order_id"),
        event_time=at(12, 4),
        reason="risk decision ALLOW rsk order gate",
        source="core.execution",
        environment="PAPER",
        correlation_id=new_identifier("correlation_id"),
        before={"status": "RISK_CHECK"},
        after={"status": "SUBMITTED"},
        risk_version="1.0.0",
    )
    defaults.update(overrides)
    return AuditRecord(**defaults)


def make_api_request(**overrides):
    defaults = dict(
        request_id=new_identifier("request_id"),
        correlation_id=new_identifier("correlation_id"),
        method="GET",
        path="/system/state",
        environment="PAPER",
        actor_id=new_identifier("user_id"),
        permission="VIEW",
        request_time=at(12, 7),
    )
    defaults.update(overrides)
    return APIRequest(**defaults)


def make_api_response(**overrides):
    defaults = dict(
        request_id=new_identifier("request_id"),
        correlation_id=new_identifier("correlation_id"),
        status="OK",
        response_time=at(12, 7),
        environment="PAPER",
        contract_versions={"api_response": "1.0.0"},
        data={"state": "RUNNING"},
    )
    defaults.update(overrides)
    return APIResponse(**defaults)


def make_config(**overrides):
    defaults = dict(
        config_id=new_identifier("config_id"),
        kind=ConfigKind.SYSTEM,
        version="1.0.0",
        data={"timezone": "UTC"},
        created_by=new_identifier("user_id"),
        created_at=at(9, 0),
    )
    defaults.update(overrides)
    return ConfigContract(**defaults)


def make_environment_declaration(**overrides):
    defaults = dict(
        environment="PAPER",
        declared_by="platform.runtime",
        declared_at=at(0, 0),
        capabilities=("order_submission",),
        restrictions=("no_live_broker",),
    )
    defaults.update(overrides)
    return EnvironmentDeclaration(**defaults)


def make_model_version():
    return ModelVersion(
        model_version_id=new_identifier("model_version_id"),
        model_id=new_identifier("model_id"),
        version="1.0.0",
        registered_at=at(8, 0),
    )


def make_model():
    return Model(model_id=new_identifier("model_id"), name="regime-classifier")


def make_ai_analysis(**overrides):
    defaults = dict(
        analysis_id=new_identifier("model_version_id"),
        model_version_id=new_identifier("model_version_id"),
        analysis_type=AIAnalysisType.MARKET_REGIME,
        environment="PAPER",
        created_at=at(12, 1),
        output={"regime": "RANGE", "strength": "0.61"},
        confidence=0.61,
        correlation_id=new_identifier("correlation_id"),
    )
    defaults.update(overrides)
    return AIAnalysis(**defaults)


def make_permission_grant(**overrides):
    defaults = dict(
        role=Role.ADMIN,
        permission=Permission.LIVE_TRADE,
        environment="LIVE",
        granted_by=new_identifier("user_id"),
        granted_at=at(8, 0),
    )
    defaults.update(overrides)
    return PermissionGrant(**defaults)
