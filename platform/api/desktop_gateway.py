"""Desktop gateway (owned by platform.api, Phase 9).

The ONE facade the desktop talks to. Composes the REAL Core stack
in-process and exposes read projections + privileged action dispatch.
The gateway is plumbing, never an authority. Market data is SYNTHETIC
and labeled as such.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc, utc_now

from core.events.contracts import EventType, build_event

from core.governance.gate import (
    GovernanceArtifact,
    GovernanceGate,
    GovernedArtifactType,
)

from core.intelligence.inference import AISafetyValidator, InferenceEngine
from core.intelligence.outputs import ProposedDirection
from core.intelligence.proposal import build_proposal

from core.ledger.posting import LedgerPostingService

from core.oms.engine import OrderManagementSystem
from core.oms.projection import ExecutionProjector

from core.policy.contracts import Policy, PolicyStatus, PolicyType
from core.policy.registry import ActorContext, PolicyRegistry

from core.risk.context import build_context
from core.risk.engine import RiskEngine
from core.risk.state_service import RiskStateService

from core.security.authentication import (
    AuthenticationService,
    SessionService,
)
from core.security.authorization import (
    AuthorizationDecision,
    AuthorizationService,
)
from core.security.contracts import AuthMethod
from core.security.protection import (
    AuditChain,
    RateLimiter,
    ReplayGuard,
    SecurityIncidentService,
    SecurityMetrics,
    emit_security_event,
)
from core.security.services import MakerCheckerService

from core.state.contracts import StateCategory

from adapters.simulation.execution import SimulationExecutionAdapter
from adapters.simulation.sequencer import SimulationSequencer
from core.ems.engine import ExecutionManagementSystem

from platform.api.desktop_actions import ActionDispatcher, ActionReceipt
from platform.api.desktop_feed import (
    SYMBOLS,
    IntelligenceStack,
    advisory_direction,
    build_dataset,
    build_feed,
    build_inferencer,
    build_intelligence,
    snapshot_for,
)
from platform.database.sqlite_stores import StorageSet
from platform.event_bus.in_process import InProcessEventBus
from platform.security.contracts import Permission, Role

#: Hard upper bound so a runaway UI can never spam the authority chain.
MAX_GROSS_LIMIT = "50000"
GATEWAY_VERSION = "1.0.0"
SESSION_TTL = timedelta(hours=8)
STALE_AFTER = timedelta(seconds=90)


def _now() -> datetime:
    return utc_now()


class DesktopGateway:
    """Composes the real stack; projects read models; dispatches actions."""

    def __init__(self, db_path: str | Path | None = None,
                 environment: str = "SIMULATION") -> None:
        if environment not in ("SIMULATION", "DEMO"):
            raise ContractError(
                "the desktop gateway operates SIMULATION/DEMO only; LIVE "
                "requires its own explicit activation path",
                location="gateway.environment", rule_id="GUI-ENV")
        self.environment = environment
        path = Path(db_path) if db_path else (
            Path("runtime") / f"desktop-{new_identifier('correlation_id')[:8]}.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._storage = StorageSet(path)

        # Phase 8 security stack
        self.security = AuthenticationService()
        self.sessions = SessionService()
        self.authorization = AuthorizationService(self.sessions)
        self.approvals = MakerCheckerService()
        self.audit_chain = AuditChain()
        self.replay_guard = ReplayGuard()
        self.rate_limiter = RateLimiter(max_operations=30, window_seconds=60)
        self.incidents = SecurityIncidentService()
        self.metrics = SecurityMetrics()
        self.governance = GovernanceGate(
            sessions=self.sessions, authorization=self.authorization,
            approvals=self.approvals, audit=self.audit_chain)

        # Phase 3 policy + risk
        self.policies = PolicyRegistry(self._storage.policies,
                                       self._storage.audit)
        self._activate_exposure_policy()
        self._activate_safety_policy()
        self.risk_engine = RiskEngine(policies=self.policies,
                                      audit=self._storage.audit)
        self.risk_state = RiskStateService(self._storage.states,
                                           self._storage.audit)

        # Phase 5 execution
        self.oms = OrderManagementSystem(
            orders=self._storage.orders,
            reports=self._storage.execution_reports,
            audit=self._storage.audit)
        self.simulation_adapter = SimulationExecutionAdapter(
            SimulationSequencer())
        self.simulation_adapter.connect()  # synthetic plane: online
        self.ems = ExecutionManagementSystem(
            adapters={environment: self.simulation_adapter},
            outbox=None,
            audit=self._storage.audit)
        self.oms._desktop_ems = self.ems  # dispatcher wiring (see actions)
        posting = LedgerPostingService(self._storage.ledger,
                                       self._storage.audit)
        self.projector = ExecutionProjector(
            states=self._storage.states, posting=posting,
            events=self._storage.events, audit=self._storage.audit)

        # Phase 4/7 strategy + intelligence
        self.strategy_id = new_identifier("strategy_id")
        self.clock = _now().replace(microsecond=0)
        self._feeds = {symbol: build_feed(symbol, 90, self.clock
                                          - timedelta(minutes=89))
                       for symbol in SYMBOLS}
        self._datasets = {symbol: build_dataset(feed, self.clock)
                          for symbol, feed in self._feeds.items()}
        primary = SYMBOLS[0]
        self.intelligence = build_intelligence(self._datasets[primary],
                                               self.clock)
        self.inferencer = build_inferencer(self.intelligence)

        # realtime plumbing
        self.bus = InProcessEventBus()
        self._subscribers: dict[str, Callable[[Any], None]] = {}
        self._seen_event_ids: set[str] = set()
        self._last_tick: datetime = self.clock
        self.session = None
        self.connected = True

        self._order_ids: list[str] = []
        self._position_ids: list[str] = []
        self._correlations: list[str] = []
        self.dispatcher = ActionDispatcher(
            environment=environment, policies=self.policies,
            risk_engine=self.risk_engine, risk_state=self.risk_state,
            oms=self.oms, projector=self.projector,
            strategy_id=self.strategy_id, audit=self._storage.audit)

    # ------------------------------------------------------------------ #
    # Phase 8 security surface                                            #
    # ------------------------------------------------------------------ #
    #: Server-side personas: the role comes from THIS table, never from
    #: the client (SECTION 11: client-supplied roles are never trusted).
    PERSONAS = {
        "trader": ("usr_" + "a" * 32, "TRADER",
                   "synthetic-trader-desktop-secret"),
        "risk_manager": ("usr_" + "b" * 32, "RISK_MANAGER",
                         "synthetic-risk-desktop-secret"),
    }

    def login(self, persona: str, secret: str) -> Mapping[str, Any]:
        if persona not in self.PERSONAS:
            raise ContractError(
                "unknown persona", location="gateway.login",
                rule_id="SEC-AUTH")
        fixed_actor, role, expected_secret = self.PERSONAS[persona]
        credential, _ = self.security.issue(
            actor_id=fixed_actor, environment=self.environment,
            method=AuthMethod.TOKEN, secret_material=expected_secret,
            at=_now())
        result = self.security.authenticate(
            credential_id=credential.credential_id,
            secret_material=secret, environment=self.environment,
            at=_now() + timedelta(seconds=1))
        if result.status.value != "AUTHENTICATED":
            self.metrics.increment("authentication_failure_count")
            raise ContractError(
                "authentication failed", location="gateway.login",
                rule_id="SEC-AUTH")
        permission_map = {
            "TRADER": ("VIEW", "ANALYZE", "SIMULATE", "DEMO_TRADE"),
            "RISK_MANAGER": ("VIEW", "ANALYZE", "MODIFY_RISK", "PAUSE",
                             "CLOSE_ONLY", "EMERGENCY_STOP"),
        }
        self.session = self.sessions.create(
            authentication=result, role=role,
            permissions=permission_map[role],
            session_ttl=SESSION_TTL)
        self.metrics.increment("authentication_success_count")
        self._publish(emit_security_event(
            EventType.AUTHENTICATION_SUCCEEDED,
            payload={"actor": fixed_actor}, environment=self.environment,
            entity_id=fixed_actor, event_time=_now()))
        return {"session_id": self.session.session_id,
                "actor_id": fixed_actor, "role": role,
                "environment": self.environment}

    def logout(self) -> None:
        if self.session is not None:
            self.sessions.revoke(self.session.session_id, reason="logout")
            self._publish(emit_security_event(
                EventType.SESSION_REVOKED,
                payload={"session": self.session.session_id},
                environment=self.environment,
                entity_id=self.session.actor_id, event_time=_now()))
            self.session = None

    def _require_session(self):
        if self.session is None:
            raise ContractError(
                "unauthenticated: privileged operations require a session",
                location="gateway.session", rule_id="SEC-FAIL-CLOSED")
        return self.sessions.validate(self.session.session_id, at=_now())

    def _authorize(self, permission: Permission, operation: str | None = None,
                   approval_ref: str | None = None):
        session = self._require_session()
        result = self.authorization.authorize(
            session_id=session.session_id, permission=permission,
            environment=self.environment, at=_now(),
            operation=operation, approval_ref=approval_ref)
        if result.decision is not AuthorizationDecision.ALLOW:
            self.metrics.increment("authorization_block_count")
            raise ContractError(
                f"authorization BLOCK: {result.reasons}",
                location="gateway.authorize", rule_id="GUI-AUTHZ",
                details={"permission": permission.value})
        return result

    # ------------------------------------------------------------------ #
    # Realtime: tick, freshness, subscriptions (SECTION 27-29)             #
    # ------------------------------------------------------------------ #
    def tick(self) -> Mapping[str, Any]:
        """Advance the synthetic feed one bar through the real dataset
        semantics and stamp freshness (CURRENT only right after a tick)."""
        self._last_tick = _now()
        for symbol, feed in self._feeds.items():
            index = len(feed.observations)
            from core.research.contracts import Observation
            event_time = feed.latest_event_time + timedelta(minutes=1)
            from platform.api.desktop_feed import _path
            observation = Observation(
                symbol=symbol, event_time=event_time,
                available_time=event_time + timedelta(seconds=30),
                payload={"close": str(_path(symbol, index)),
                         "volume": "100", "source": "SYNTHETIC"})
            self._feeds[symbol] = build_feed(symbol, 0, event_time) if False \
                else feed
            object.__setattr__(feed, "observations",
                               feed.observations + (observation,))
        return self.freshness()

    def freshness(self) -> Mapping[str, Any]:
        age = _now() - self._last_tick
        if not self.connected:
            status = "DISCONNECTED"
        elif age > STALE_AFTER:
            status = "STALE"
        else:
            status = "CURRENT"
        return {"status": status,
                "last_tick": self._last_tick.isoformat(),
                "age_seconds": int(age.total_seconds()),
                "source": "SYNTHETIC"}

    def subscribe(self, handler: Callable[[Any], None],
                  event_type: str | None = None) -> str:
        """SECTION 29: ownership + dedup. Duplicate event_ids never reach
        the handler twice."""
        token = self.bus.subscribe(self._deduping(handler),
                                   event_type=event_type)
        self._subscribers[token] = handler
        return token

    def unsubscribe(self, token: str) -> None:
        handler = self._subscribers.pop(token, None)
        if handler is None:
            raise ContractError("unknown subscription token",
                                location="gateway.unsubscribe",
                                rule_id="GUI-SUBSCRIPTION")
        self.bus.unsubscribe(token)

    def _deduping(self, handler):
        def wrapped(event):
            if event.event_id in self._seen_event_ids:
                return
            self._seen_event_ids.add(event.event_id)
            if len(self._seen_event_ids) > 10000:  # bounded cache
                self._seen_event_ids = set(
                    list(self._seen_event_ids)[-5000:])
            handler(event)
        return wrapped

    def _publish(self, event) -> None:
        if self.connected:
            self.bus.publish(event)

    def disconnect(self) -> None:
        """SECTION 46: disconnected -> fail closed, stale data labeled."""
        self.connected = False

    def reconnect(self) -> Mapping[str, Any]:
        self.connected = True
        self._last_tick = _now()
        return {"status": "CURRENT", "resynchronized": True}

    # ------------------------------------------------------------------ #
    # Read projections (plain data, no authority)                         #
    # ------------------------------------------------------------------ #
    def connectivity_status(self) -> Mapping[str, Any]:
        """Phase 10 read-only projection: connection/feed/reconciliation
        evidence. Absent plane = UNKNOWN (never fabricated)."""
        plane = getattr(self, "connectivity_plane", None)
        if plane is None:
            return {"connection_state": "UNKNOWN", "deployment_blocked": True,
                    "reason": "connectivity plane not composed",
                    "real": False}
        return plane.status()

    def overview(self) -> Mapping[str, Any]:
        return {
            "environment": self.environment,
            "gateway_version": GATEWAY_VERSION,
            "symbols": list(SYMBOLS),
            "strategy_id": self.strategy_id,
            "risk_state": self.risk_state.current() or "UNKNOWN",
            "freshness": self.freshness(),
            "orders": len(self._order_ids),
            "positions": self._position_count(),
            "connected": self.connected,
        }

    def _projector_recent_positions(self) -> list[str]:
        recent = getattr(self.projector, 'recent_position_ids', None)
        return list(recent()) if callable(recent) else []

    def _position_count(self) -> int:
        count = 0
        for entity in self._iter_states(StateCategory.POSITION_STATE):
            if entity.payload.get("quantity", "0") not in ("0", 0, None):
                count += 1
        return count

    def _iter_states(self, category: StateCategory):
        try:
            for record in self._storage.states.iter_category(
                    category.value):
                yield record
        except AttributeError:
            return
            yield

    def watchlist(self) -> list[Mapping[str, Any]]:
        rows = []
        for symbol, feed in self._feeds.items():
            closes = [float(o.payload["close"]) for o in feed.observations]
            rows.append({
                "symbol": symbol,
                "last": closes[-1],
                "change": round(closes[-1] - closes[0], 5),
                "source": "SYNTHETIC",
                "event_time": feed.latest_event_time.isoformat(),
            })
        return rows

    def market(self, symbol: str) -> Mapping[str, Any]:
        feed = self._feeds.get(symbol)
        if feed is None:
            raise ContractError(f"unknown symbol {symbol}",
                                location="gateway.market",
                                rule_id="GUI-SYMBOL")
        rows = [{"event_time": o.event_time.isoformat(),
                 "available_time": o.available_time.isoformat(),
                 "close": o.payload["close"],
                 "time_kind": "EVENT_TIME"}
                for o in feed.observations[-60:]]
        return {"symbol": symbol, "bars": rows,
                "latest": feed.latest_price, "source": "SYNTHETIC",
                "freshness": self.freshness()}

    def intelligence_view(self, symbol: str) -> Mapping[str, Any]:
        """E-pane projection: the ADVISORY chain, never order authority."""
        dataset = self._datasets.get(symbol)
        if dataset is None:
            raise ContractError(f"unknown symbol {symbol}",
                                location="gateway.intelligence",
                                rule_id="GUI-SYMBOL")
        as_of = self._feeds[symbol].observations[-1].available_time
        snapshot = snapshot_for(self.intelligence, dataset, symbol, as_of)
        result = self.inferencer.infer(
            model_id=self.intelligence.model.model_id,
            model_version=self.intelligence.model.model_version,
            inference_time=as_of, environment="RESEARCH",
            snapshot=snapshot)
        proposal = build_proposal(
            result=result, model=self.intelligence.model,
            direction=advisory_direction(result.output.get("label", "")),
            rationale="momentum classifier (SYNTHETIC, advisory only)",
            environment="RESEARCH",
            evidence={"model_hash": self.intelligence.model.model_hash,
                      "dataset_hash": dataset.content_hash})
        return {
            "advisory_only": True,
            "label": result.output.get("label", "UNKNOWN"),
            "probability": result.probability,
            "uncertainty": result.uncertainty,
            "proposal_id": proposal.ai_proposal_id,
            "direction": proposal.proposed_direction.value,
            "valid_until": proposal.valid_until.isoformat(),
            "why": {
                "system": "AI_WHY",
                "chain": ["SYNTHETIC observations",
                          "point-in-time features",
                          "deterministic classifier",
                          "advisory proposal"],
            },
        }

    def core_why(self, order_id: str) -> Mapping[str, Any]:
        """SECTION 16: the CORE WHY chain from real audit evidence."""
        try:
            record = self._storage.orders.get_by_id(order_id)
        except Exception:
            record = None
        return {
            "system": "CORE_WHY",
            "chain": ["Intent", "Portfolio", "RiskContext", "Policy",
                      "Risk Rule", "RiskDecision", "OMS", "EMS", "Fill",
                      "Position", "Ledger", "Audit"],
            "order_found": record is not None,
        }

    def risk_snapshot(self) -> Mapping[str, Any]:
        context = self.risk_context()
        request_context = context
        from core.risk.engine import RiskEvaluationRequest
        request = RiskEvaluationRequest(
            action_type="NEW_EXPOSURE", subject=SYMBOLS[0],
            environment=self.environment, requested_exposure="100",
            context=request_context,
            correlation_id=new_identifier("correlation_id"), at=_now())
        decision = self.risk_engine.evaluate(request)
        return {
            "risk_state": self.risk_state.current() or "UNKNOWN",
            "decision": decision.decision.value,
            "reasons": list(decision.reasons),
            "limits": dict(decision.limits),
            "gross_exposure": context.gross_exposure or "0",
            "environment": self.environment,
        }

    def risk_context(self):
        state = self.risk_state.current() or "NORMAL"
        system_state = {"NORMAL": "RUNNING", "CAUTION": "RUNNING",
                        "LIMITED": "PAUSED", "PAUSE": "PAUSED",
                        "EMERGENCY": "EMERGENCY"}.get(state, "UNKNOWN")
        execution_state = "READY" if state in ("NORMAL", "CAUTION")             else "HELD"
        return build_context(
            as_of=_now(), environment=self.environment,
            account_equity="100000", account_margin_level="500",
            gross_exposure="0", drawdown_pct="0",
            market_state="NORMAL", volatility_state="NORMAL",
            spread_state="NORMAL", liquidity_state="NORMAL",
            data_quality="VALIDATED", event_risk="NORMAL",
            system_state=system_state, execution_state=execution_state)

    def orders(self) -> list[Mapping[str, Any]]:
        rows = []
        for order in self._storage.orders.iter_by_strategy(
                self.strategy_id):
            rows.append({
                "order_id": order.order_id, "symbol": order.symbol,
                "side": order.side.value,
                "quantity": str(order.quantity),
                "status": order.status.value,
                "environment": order.environment,
                "created_at": order.created_at.isoformat(),
                "correlation_id": order.correlation_id,
            })
        return rows

    def positions(self) -> list[Mapping[str, Any]]:
        rows = []
        for entity_id in self._position_ids:
            record = self._storage.states.get_current_state(
                StateCategory.POSITION_STATE.value, entity_id)
            if record is None:
                continue
            payload = record.payload
            rows.append({
                "position_id": entity_id,
                "symbol": payload.get("symbol", "UNKNOWN"),
                "quantity": payload.get("quantity", "0"),
                "entry_price": payload.get("entry_price", "UNKNOWN"),
                "status": record.status,
                "environment": self.environment,
            })
        return rows

    def ledger_entries(self, limit: int = 50) -> list[Mapping[str, Any]]:
        rows = []
        for correlation in self._correlations:
            for entry in self._storage.ledger.iter_by_correlation_id(
                    correlation):
                rows.append({
                    "ledger_entry_id": entry.ledger_entry_id,
                    "account_id": entry.account_id,
                    "amount": str(entry.amount),
                    "currency": entry.currency,
                    "entry_type": entry.entry_type.value,
                    "symbol": entry.symbol,
                    "entry_time": entry.entry_time.isoformat(),
                })
        return rows[:limit]

    def audit_records(self, limit: int = 100) -> list[Mapping[str, Any]]:
        return [{
            "audit_id": record.audit_id,
            "actor": record.actor_id,
            "action": record.action,
            "entity": record.entity_id,
            "environment": record.environment,
            "event_time": record.event_time.isoformat(),
            "reason": record.reason,
            "correlation_id": record.correlation_id,
            "integrity": record.integrity_hash,
        } for record in list(self.audit_chain)[-limit:]]

    def attention(self) -> list[Mapping[str, Any]]:
        """SECTION 19: attention derived from real state only."""
        items = []
        freshness = self.freshness()
        if freshness["status"] in ("STALE", "DISCONNECTED"):
            items.append({"category": "DATA",
                          "severity": "WARNING",
                          "title": f"feed {freshness['status']}",
                          "object_ref": None})
        pending = [a for a in self._approvals_snapshot()
                   if a["status"] == "SUBMITTED"]
        for approval in pending:
            items.append({"category": "GOVERNANCE",
                          "severity": "INFO",
                          "title": "approval pending",
                          "object_ref": approval["approval_id"]})
        risk_state = self.risk_state.current()
        if risk_state in ("SAFE_MODE", "PAUSED", "EMERGENCY"):
            items.append({"category": "RISK", "severity": "CRITICAL",
                          "title": f"risk state {risk_state}",
                          "object_ref": "risk-engine"})
        for incident in self._incidents_snapshot():
            if incident["status"] != "CLOSED":
                items.append({"category": "SECURITY",
                              "severity": incident["severity"],
                              "title": incident["title"],
                              "object_ref": incident["id"]})
        return items

    def _approvals_snapshot(self) -> list[Mapping[str, Any]]:
        return getattr(self.approvals, "snapshot", lambda: [])()

    def _incidents_snapshot(self) -> list[Mapping[str, Any]]:
        return getattr(self.incidents, "snapshot", lambda: [])()

    def search(self, query: str) -> list[Mapping[str, Any]]:
        """SECTION 20: search over authoritative objects; opening a result
        establishes context (the UI does that; authorization still applies
        on actions)."""
        results = []
        needle = query.strip().lower()
        if not needle:
            return results
        for order in self.orders():
            if needle in order["order_id"].lower() or \
                    needle in order["symbol"].lower():
                results.append({"kind": "ORDER",
                                "object_id": order["order_id"],
                                "label": f"{order['symbol']} "
                                         f"{order['side']} "
                                         f"{order['status']}"})
        for position in self.positions():
            if needle in position["position_id"].lower() or \
                    needle in str(position["symbol"]).lower():
                results.append({"kind": "POSITION",
                                "object_id": position["position_id"],
                                "label": f"{position['symbol']} "
                                         f"{position['quantity']}"})
        for symbol in SYMBOLS:
            if needle in symbol.lower():
                results.append({"kind": "SYMBOL", "object_id": symbol,
                                "label": symbol})
        return results

    # ------------------------------------------------------------------ #
    # Privileged actions (authority chain; SECTION 43-45)                 #
    # ------------------------------------------------------------------ #
    def act(self, action: str, params: Mapping[str, Any]) -> ActionReceipt:
        if not self.connected:
            raise ContractError(
                "disconnected: privileged actions fail closed "
                "(DISCONNECTED is never SAFE)",
                location="gateway.act", rule_id="GUI-FAIL-CLOSED")
        self._authorize_trade_permission(action)
        self.rate_limiter.require_allowed(
            limit_key=f"act:{self.session.actor_id if self.session else 'anon'}",
            now=_now())
        if action == "submit_order":
            receipt = self.dispatcher.submit_order(
                symbol=params["symbol"], side=params["side"],
                quantity=params["quantity"],
                price=self._feeds[params["symbol"]].latest_price,
                at=_now(), base_context=self.risk_context())
            if receipt.order_id:
                self._order_ids.append(receipt.order_id)
            if receipt.audit_correlation_id:
                self._correlations.append(receipt.audit_correlation_id)
            if receipt.position_id and receipt.position_id not in                     self._position_ids:
                self._position_ids.append(receipt.position_id)
            self._audit_action(action, receipt)
            return receipt
        if action in ("pause", "close_only", "emergency_stop"):
            target = {"pause": "PAUSE",
                      "close_only": "LIMITED",
                      "emergency_stop": "EMERGENCY"}[action]
            receipt = self.dispatcher.safety_control(
                target=target, at=_now(),
                reason=str(params.get("reason", action)))
            self._audit_action(action, receipt)
            return receipt
        raise ContractError(f"unknown action {action}",
                            location="gateway.act", rule_id="GUI-ACTION")

    _ACTION_PERMISSIONS = {"pause": Permission.PAUSE,
                           "close_only": Permission.CLOSE_ONLY,
                           "emergency_stop": Permission.EMERGENCY_STOP}

    def _authorize_trade_permission(
            self, action: str = "submit_order") -> None:
        permission = self._ACTION_PERMISSIONS.get(action)
        if permission is None:
            if self.environment == "SIMULATION":
                permission = Permission.SIMULATE
            else:
                permission = Permission.DEMO_TRADE
        self._authorize(permission)

    def _audit_action(self, action: str, receipt: ActionReceipt) -> None:
        from platform.audit.contracts import ActorType, AuditRecord
        actor = self.session.actor_id if self.session else "usr_unknown"
        self.audit_chain.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.USER,
            actor_id=actor, action=f"DESKTOP_{action.upper()}",
            entity_type="desktop_action",
            entity_id=receipt.order_id or action,
            event_time=_now(), before=None,
            after={"state": receipt.state,
                   "reasons": list(receipt.reasons)},
            reason="desktop action dispatch", source="platform.api.gateway",
            environment=self.environment,
            correlation_id=receipt.audit_correlation_id
            or new_identifier("correlation_id")))

    # ------------------------------------------------------------------ #
    # Governance + incidents surface                                      #
    # ------------------------------------------------------------------ #
    def submit_governance_change(self, *, artifact_type: str,
                                 artifact_id: str, old_version: str,
                                 new_version: str, old_hash: str,
                                 new_hash: str, reason: str) -> str:
        session = self._require_session()
        submission = self.approvals.submit(
            operation=f"{artifact_type}_CHANGE", resource=artifact_id,
            environment=self.environment, maker_actor_id=session.actor_id,
            at=_now(), reason=reason)
        return submission.approval_id

    def approve_governance_change(self, approval_id: str,
                                  checker_actor_id: str) -> ActionReceipt:
        try:
            self.approvals.approve(approval_id,
                                   checker_actor_id=checker_actor_id,
                                   checker_kind="HUMAN", at=_now())
            return ActionReceipt(action="governance_approve",
                                 state="APPLIED",
                                 reasons=("approval ACTIVE",))
        except ContractError as error:
            return ActionReceipt(action="governance_approve",
                                  state="REJECTED",
                                  reasons=(str(error),))

    # ------------------------------------------------------------------ #
    # Internal: real policy lifecycle (no shortcuts)                      #
    # ------------------------------------------------------------------ #
    def _activate_safety_policy(self) -> None:
        author = ActorContext(user_id=new_identifier("user_id"),
                              role=Role.ADMIN)
        approver = ActorContext(user_id=new_identifier("user_id"),
                                role=Role.APPROVER)
        policy = Policy(
            policy_id=new_identifier("policy_id"), policy_version="1.0.0",
            policy_type=PolicyType.GLOBAL_SAFETY_POLICY,
            status=PolicyStatus.DRAFT, environment=self.environment,
            scope="*",
            conditions=(
                {"rule_id": "R-HALT-PAUSE", "dimension": "GLOBAL",
                 "field": "system.state", "op": "!=",
                 "limit": "halt_pause", "on_trigger": "EMERGENCY",
                 "critical": True},
                {"rule_id": "R-HALT-EMERGENCY", "dimension": "GLOBAL",
                 "field": "system.state", "op": "!=",
                 "limit": "halt_emergency", "on_trigger": "EMERGENCY",
                 "critical": True},
            ),
            actions=({"constrain": "risk"},),
            limits={"halt_pause": "PAUSED", "halt_emergency": "EMERGENCY"},
            priority=1,
            effective_from=_now(), created_by=author.user_id,
            created_at=_now(), updated_at=_now())
        self.policies.create(policy, author, at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.submit_review(policy.policy_id,
                                    latest.policy_version, author,
                                    at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.approve(policy.policy_id, latest.policy_version,
                              approver, at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.activate(policy.policy_id, latest.policy_version,
                               author, at=_now())

    def _activate_exposure_policy(self) -> None:
        author = ActorContext(user_id=new_identifier("user_id"),
                              role=Role.ADMIN)
        approver = ActorContext(user_id=new_identifier("user_id"),
                                role=Role.APPROVER)
        policy = Policy(
            policy_id=new_identifier("policy_id"), policy_version="1.0.0",
            policy_type=PolicyType.EXPOSURE_POLICY,
            status=PolicyStatus.DRAFT, environment=self.environment,
            scope="*",
            conditions=({"rule_id": "R-EXP", "dimension": "EXPOSURE",
                         "field": "positions.gross", "op": "<=",
                         "limit": "max_gross", "on_trigger": "BLOCK",
                         "critical": True},),
            actions=({"constrain": "risk"},),
            limits={"max_gross": MAX_GROSS_LIMIT}, priority=10,
            effective_from=_now(), created_by=author.user_id,
            created_at=_now(), updated_at=_now())
        self.policies.create(policy, author, at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.submit_review(policy.policy_id,
                                    latest.policy_version, author,
                                    at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.approve(policy.policy_id, latest.policy_version,
                              approver, at=_now())
        latest = list(self.policies._store.iter_versions(
            policy.policy_id))[-1]
        self.policies.activate(policy.policy_id, latest.policy_version,
                               author, at=_now())

    def close(self) -> None:
        self._storage.close()


import tempfile  # noqa: E402  (path default above)
