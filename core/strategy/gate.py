"""Intent gate: Strategy -> Portfolio -> Risk boundary (owned by core.strategy
together with core.portfolio, SECTION 29/62).

STATE -> STRATEGY EVALUATION -> INTENT -> PORTFOLIO EVALUATION -> PROJECTED
EXPOSURE -> RISK CONTEXT -> RISK ENGINE (the SAME Phase 3 engine - there is
no second risk engine) -> RISK DECISION -> INTENT PERMISSION.

The gate NEVER creates orders; the risk engine is re-invoked with the
projected exposure so the final permission reflects portfolio-level risk."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from architecture.contracts.errors import ContractValidationError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.portfolio.decision import PortfolioDecision
from core.portfolio.exposure import ExposureLeg
from core.risk.contracts import RiskDecision, RiskResult
from core.risk.engine import RiskEngine, RiskEvaluationRequest
from core.risk.context import build_context
from core.strategy.intent import StrategyIntent
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"

RISK_INCREASING = ("OPEN", "INCREASE")


@dataclass(frozen=True)
class IntentPermission:
    intent_id: str
    permission: str            # final permission for THIS intent
    constraints: Mapping[str, str] | None
    portfolio_decision: PortfolioDecision
    risk_decision: RiskDecision
    reasons: tuple[str, ...]

    @property
    def permitted(self) -> bool:
        return self.permission in ("ALLOW", "LIMITED")


class IntentGate:
    """Deterministic gate. Re-evaluates risk with projected exposure through
    the SAME RiskEngine (SECTION 62: risk appears before AND after strategy/
    portfolio, but there is exactly one engine)."""

    def __init__(self, risk_engine: RiskEngine, audit: AuditRepository | None = None) -> None:
        self._risk = risk_engine
        self._audit = audit

    def authorize(
        self,
        *,
        intent: StrategyIntent,
        portfolio_decision: PortfolioDecision,
        base_context,  # core.risk.context.RiskContext (pre-portfolio)
        at: datetime,
    ) -> IntentPermission:
        intent.validate()
        portfolio_decision.validate()
        moment = ensure_utc(at, location="gate.at")
        if intent.is_expired(moment):
            raise RiskGateError(
                f"Intent {intent.intent_id} expired at "
                f"{ensure_utc(intent.expires_at).isoformat()}",
                location="gate.intent_expiry", rule_id="INTENT-002",
            )

        # 1. environment gate: intent environment must match the portfolio decision's
        if intent.environment != portfolio_decision.environment:
            raise RiskGateError(
                f"Intent environment {intent.environment} does not match portfolio decision "
                f"environment {portfolio_decision.environment} (fail closed)",
                location="gate.environment", rule_id="ENV-001",
            )

        # 1b. intent vs portfolio decision: strategy must be in the decision's active set
        if intent.strategy_id not in portfolio_decision.active_strategies:
            raise RiskGateError(
                f"Intent strategy {intent.strategy_id} is not active in portfolio decision",
                location="gate.strategy_active", rule_id="PORTFOLIO-001",
            )

        # 2. projected exposure context: base context + portfolio projection
        projected = dict(portfolio_decision.projected_exposure)
        base_gross = base_context.gross_exposure or "0"
        projected_gross = str(projected.get("gross", base_gross))
        # projected exposure must never UNDERSTATE known account-level exposure:
        # the risk engine sees the larger of the two (fail closed)
        from core.ledger.money import parse_decimal as _pd

        gross = str(max(_pd(base_gross, location="gate.gross.base"),
                        _pd(projected_gross, location="gate.gross.projected")))
        override = {"gross_exposure": gross}
        if intent.symbol in projected.get("by_symbol", {}):
            # symbol exposure rides in symbol_exposure for rule resolution
            current_symbol = dict(base_context.symbol_exposure or {})
            current_symbol[intent.symbol] = projected["by_symbol"][intent.symbol]
            override["symbol_exposure"] = current_symbol
        values = base_context.to_content_values()
        values.pop("as_of", None)
        values.pop("environment", None)
        values.update(override)
        projected_context = build_context(
            as_of=base_context.as_of, environment=intent.environment, **values,
        )

        # 3. SAME risk engine re-evaluation with projected exposure
        decision = self._risk.evaluate(RiskEvaluationRequest(
            action_type=intent.intent_type.value,
            subject=intent.symbol,
            environment=intent.environment,
            requested_exposure=intent.requested_quantity,
            context=projected_context,
            correlation_id=intent.correlation_id,
            at=moment,
            account_id=None,
            symbol=intent.symbol,
            strategy_id=intent.strategy_id,
            causation_id=intent.intent_id,
        ))

        # 4. intent-direction semantics against the final permission
        permission = decision.decision.value
        constraints = dict(decision.permission_constraints) if decision.permission_constraints else None
        if intent.intent_type.value in RISK_INCREASING and permission in ("BLOCK", "CLOSE_ONLY", "EMERGENCY"):
            reasons = tuple(decision.reasons) + (
                f"INTENT_{intent.intent_type.value}_BLOCKED_BY_{permission}",)
        else:
            reasons = tuple(decision.reasons)

        result = IntentPermission(
            intent_id=intent.intent_id,
            permission=permission,
            constraints=constraints,
            portfolio_decision=portfolio_decision,
            risk_decision=decision,
            reasons=reasons,
        )
        if self._audit is not None:
            self._audit.append(AuditRecord(
                audit_id=new_identifier("audit_id"), actor_type=ActorType.SYSTEM,
                actor_id="core.strategy.gate", action="INTENT_AUTHORIZATION",
                entity_type="strategy_intent", entity_id=intent.intent_id,
                event_time=moment, before=None,
                after={"permission": permission, "strategy": intent.strategy_id,
                       "symbol": intent.symbol},
                reason=f"intent -> portfolio -> risk -> {permission}",
                source="core.strategy.gate", environment=intent.environment,
                correlation_id=intent.correlation_id, causation_id=intent.intent_id,
            ))
        return result
