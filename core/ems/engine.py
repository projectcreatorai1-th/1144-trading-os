"""EMS - Execution Management System (owned by core.ems).

Routes validated OMS orders to the right ExecutionAdapter. Handles
submission, timeout (-> UNKNOWN, never FAILED without evidence), retry
policy (deterministic classification), durable outbox and route audit. EMS
never creates strategies/portfolios, never overrides risk, never fabricates
fills (SECTION 15)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from architecture.contracts.environment import assert_same_environment
from architecture.contracts.errors import ContractError, RiskGateError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc
from core.ems.adapter import AdapterResponse, ExecutionAdapter
from core.ems.outbox import DeliveryStatus, OutboxMessage, OutboxStore
from core.execution.boundary import BoundaryResult, ExecutionContext, ExecutionDecision, can_execute
from core.execution.contracts import Order, OrderStatus
from core.oms.contracts import BrokerError, RetryClassification, classify_retry
from platform.audit.contracts import ActorType, AuditRecord
from platform.audit.repository import AuditRepository

CONTRACT_VERSION = "1.0.0"


class EMSError(ContractError):
    rule_id = "EMS-001"


@dataclass(frozen=True)
class SubmissionResult:
    order: Order
    submitted: bool
    state: str            # final order status after submission attempt
    broker_order_id: str | None
    normalized_error: str | None
    raw_reference: str | None
    retry_classification: str | None


class ExecutionManagementSystem:
    def __init__(self, *, adapters: Mapping[str, ExecutionAdapter],
                 outbox: OutboxStore | None = None,
                 audit: AuditRepository | None = None) -> None:
        self._adapters = dict(adapters)
        self._outbox = outbox
        self._audit = audit

    def register_adapter(self, environment: str, adapter: ExecutionAdapter) -> None:
        self._adapters[environment] = adapter

    # ------------------------------------------------------------------ #
    # Routing                                                             #
    # ------------------------------------------------------------------ #
    def _adapter_for(self, environment: str) -> ExecutionAdapter:
        adapter = self._adapters.get(environment)
        if adapter is None:
            raise EMSError(
                f"No execution adapter registered for environment '{environment}' "
                "(fail closed - never fall back to another environment)",
                location="ems.adapter_for", rule_id="ENV-004",
            )
        assert_same_environment(environment, adapter.environment(),
                                context=f"ems.adapter_environment[{environment}]")
        return adapter

    # ------------------------------------------------------------------ #
    # Submission                                                          #
    # ------------------------------------------------------------------ #
    def submit(self, order: Order, *, risk_decision,
               execution_context: ExecutionContext) -> SubmissionResult:
        """Last-moment validation (can_execute) then adapter submission.
        Timeouts/unknowns produce UNKNOWN - never FAILED (SECTION 26/27)."""
        if order.status not in (OrderStatus.ACCEPTED, OrderStatus.ROUTING):
            raise EMSError(
                f"EMS accepts validated OMS orders only (status {order.status.value})",
                location="ems.submit", rule_id="EMS-001",
            )
        boundary = can_execute(order, risk_decision, execution_context)
        if boundary.decision is not ExecutionDecision.ALLOW:
            self._audit_route(order, "EXECUTION_REJECTED",
                              reason=";".join(boundary.reasons))
            raise RiskGateError(
                f"can_execute rejected submission: {boundary.reasons}",
                location="ems.submit", rule_id="EXEC-001",
                details={"reasons": list(boundary.reasons)},
            )

        adapter = self._adapter_for(order.environment)
        capability = adapter.capabilities()
        capability.validate()
        if not capability.execution_safe:
            raise RiskGateError(
                "Adapter capability UNKNOWN blocks execution (position semantics unknown)",
                location="ems.submit", rule_id="EXEC-003",
            )
        if not capability.supports(symbol=order.symbol,
                                   order_type=order.order_type.value,
                                   time_in_force=order.time_in_force.value):
            raise RiskGateError(
                f"Adapter does not support {order.symbol}/{order.order_type.value}/"
                f"{order.time_in_force.value}",
                location="ems.submit", rule_id="EXEC-003",
            )

        canonical_request = self._canonical_request(order)
        self._enqueue_outbox(order, "ORDER_SUBMIT", canonical_request)

        response = adapter.submit_order(canonical_request)
        response.validate()
        self._audit_route(order, "ORDER_SUBMITTED",
                          broker_order_id=response.broker_order_id,
                          raw_reference=response.raw_reference,
                          ok=response.ok)
        if response.ok:
            return SubmissionResult(
                order=order, submitted=True, state="ACKNOWLEDGED",
                broker_order_id=response.broker_order_id,
                normalized_error=None, raw_reference=response.raw_reference,
                retry_classification=None,
            )
        error = BrokerError(response.normalized_error or "UNKNOWN")
        classification = classify_retry(error)
        if classification is not RetryClassification.NON_RETRYABLE:
            # timeout/connection/unknown outcome: state UNKNOWN until
            # reconciliation determines the truth (SECTION 26/27) - never FAILED
            return SubmissionResult(
                order=order, submitted=False, state="UNKNOWN",
                broker_order_id=response.broker_order_id,
                normalized_error=error.value,
                raw_reference=response.raw_reference,
                retry_classification=classification.value,
            )
        return SubmissionResult(
            order=order, submitted=False, state="REJECTED",
            broker_order_id=response.broker_order_id,
            normalized_error=error.value,
            raw_reference=response.raw_reference,
            retry_classification=classification.value,
        )

    def poll(self, order: Order, broker_order_id: str) -> AdapterResponse:
        """Query broker state for reconciliation (timeout/UNKNOWN recovery)."""
        adapter = self._adapter_for(order.environment)
        response = adapter.poll_order(broker_order_id)
        response.validate()
        self._audit_route(order, "ORDER_POLLED", broker_order_id=broker_order_id,
                          raw_reference=response.raw_reference, ok=response.ok)
        return response

    def cancel(self, order: Order, broker_order_id: str) -> AdapterResponse:
        adapter = self._adapter_for(order.environment)
        response = adapter.cancel_order(broker_order_id)
        response.validate()
        self._audit_route(order, "ORDER_CANCEL_SENT", broker_order_id=broker_order_id,
                          raw_reference=response.raw_reference, ok=response.ok)
        return response

    # ------------------------------------------------------------------ #
    # Canonical request mapping (adapter-agnostic, MT5 semantics stay out) #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _canonical_request(order: Order) -> dict:
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "side": order.side.value,
            "order_type": order.order_type.value,
            "quantity": str(order.quantity),
            "price": str(order.price) if order.price is not None else None,
            "time_in_force": order.time_in_force.value,
            "environment": order.environment,
            "idempotency_key": order.idempotency_key,
        }

    def _enqueue_outbox(self, order: Order, event_type: str, payload: dict) -> None:
        if self._outbox is None:
            return
        import hashlib
        import json

        message = OutboxMessage(
            message_id=new_identifier("outbox_message_id"),
            aggregate_id=order.order_id,
            event_type=event_type,
            payload=payload,
            payload_hash=hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")).hexdigest(),
            created_at=ensure_utc(order.updated_at, location="ems.outbox"),
            delivery_status=DeliveryStatus.PENDING,
            attempt_count=0,
            environment=order.environment,
        )
        message.validate()
        self._outbox.append(message)

    def _audit_route(self, order: Order, action: str, *, broker_order_id=None,
                     raw_reference=None, ok=None, reason=None) -> None:
        if self._audit is None:
            return
        self._audit.append(AuditRecord(
            audit_id=new_identifier("audit_id"), actor_type=ActorType.SYSTEM,
            actor_id="core.ems", action=action, entity_type="order",
            entity_id=order.order_id,
            event_time=ensure_utc(order.updated_at, location="ems.audit"),
            before=None,
            after={"broker_order_id": broker_order_id, "raw_reference": raw_reference,
                   "ok": ok},
            reason=reason or action.lower(),
            source="core.ems", environment=order.environment,
            correlation_id=order.correlation_id,
        ))
