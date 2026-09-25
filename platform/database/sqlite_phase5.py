"""SQLite storage for Phase 5 (owned by platform.database).

Orders (immutable versions), execution reports (idempotent) and outbox
messages. All append-only except the outbox delivery status (at-least-once
delivery bookkeeping)."""
from __future__ import annotations

import json
import sqlite3
from typing import Iterator

from architecture.contracts.errors import StorageError
from architecture.contracts.time import canonical, parse_canonical
from core.ems.outbox import DeliveryStatus, OutboxMessage, OutboxStore
from core.execution.contracts import (
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from core.execution.store import OrderStore
from core.oms.contracts import (
    BrokerError,
    ExecutionReport,
    ExecutionStatus,
    ExecutionType,
)
from core.oms.stores import ExecutionReportStore

CONTRACT_VERSION = "1.0.0"

TERMINAL_STATUSES = frozenset({
    "FILLED", "CANCELLED", "REJECTED", "EXPIRED", "REPLACED", "CLOSED", "FAILED",
})


def _dump(content: object) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)


class SqliteOrderStore(OrderStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def save(self, order: Order) -> None:
        order.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO orders (order_id, order_version, status, environment,"
                    " idempotency_key, content) VALUES (?,?,?,?,?,?)",
                    (order.order_id, order.order_version, order.status.value,
                     order.environment, order.idempotency_key,
                     _dump(OrderStorage.to_content(order))),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Order {order.order_id} v{order.order_version} or idempotency key "
                "already stored (orders are immutable versions; duplicates rejected)",
                location="sqlite.order.save",
                details={"order_id": order.order_id, "version": order.order_version},
            ) from exc

    def get_by_id(self, order_id: str) -> Order:
        row = self._db.execute(
            "SELECT content FROM orders WHERE order_id = ? ORDER BY order_version DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Order '{order_id}' not found", location="sqlite.order.get_by_id",
            )
        return OrderStorage.from_content(json.loads(row[0]))

    def get_version(self, order_id: str, order_version: int) -> Order:
        row = self._db.execute(
            "SELECT content FROM orders WHERE order_id = ? AND order_version = ?",
            (order_id, order_version),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Order '{order_id}' v{order_version}' not found",
                location="sqlite.order.get_version",
            )
        return OrderStorage.from_content(json.loads(row[0]))

    def iter_versions(self, order_id: str) -> Iterator[Order]:
        for row in self._db.execute(
            "SELECT content FROM orders WHERE order_id = ? ORDER BY order_version",
            (order_id,),
        ):
            yield OrderStorage.from_content(json.loads(row[0]))

    def find_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        row = self._db.execute(
            "SELECT content FROM orders WHERE idempotency_key = ?"
            " ORDER BY order_version DESC LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        return None if row is None else OrderStorage.from_content(json.loads(row[0]))

    def iter_by_strategy(self, strategy_id: str) -> Iterator[Order]:
        for row in self._db.execute(
            "SELECT content FROM orders WHERE seq IN"
            " (SELECT MAX(seq) FROM orders GROUP BY order_id) ORDER BY seq"
        ):
            order = OrderStorage.from_content(json.loads(row[0]))
            if order.strategy_id == strategy_id:
                yield order

    def iter_active(self) -> Iterator[Order]:
        for row in self._db.execute(
            "SELECT content FROM orders WHERE seq IN"
            " (SELECT MAX(seq) FROM orders GROUP BY order_id) ORDER BY seq"
        ):
            order = OrderStorage.from_content(json.loads(row[0]))
            if order.status.value not in TERMINAL_STATUSES:
                yield order

    def count(self) -> int:
        return int(self._db.execute(
            "SELECT COUNT(*) FROM orders WHERE seq IN"
            " (SELECT MAX(seq) FROM orders GROUP BY order_id)").fetchone()[0])


class OrderStorage:
    """Order serialization lives with the contract owner (same pattern as
    Phase 2 normalized records)."""

    @staticmethod
    def to_content(order: Order) -> dict:
        return {
            "order_id": order.order_id, "client_order_id": order.client_order_id,
            "strategy_id": order.strategy_id, "symbol": order.symbol,
            "side": order.side.value, "order_type": order.order_type.value,
            "quantity": str(order.quantity),
            "price": str(order.price) if order.price is not None else None,
            "stop_loss": str(order.stop_loss) if order.stop_loss is not None else None,
            "take_profit": str(order.take_profit) if order.take_profit is not None else None,
            "time_in_force": order.time_in_force.value,
            "environment": order.environment, "policy_id": order.policy_id,
            "status": order.status.value, "source": order.source,
            "created_at": canonical(order.created_at),
            "updated_at": canonical(order.updated_at),
            "correlation_id": order.correlation_id,
            "risk_decision_id": order.risk_decision_id,
            "causation_id": order.causation_id,
            "account_id": order.account_id, "intent_id": order.intent_id,
            "portfolio_decision_id": order.portfolio_decision_id,
            "risk_decision_hash": order.risk_decision_hash,
            "policy_version": order.policy_version,
            "risk_context_hash": order.risk_context_hash,
            "idempotency_key": order.idempotency_key,
            "expires_at": canonical(order.expires_at) if order.expires_at else None,
            "order_version": order.order_version,
            "provenance": dict(order.provenance) if order.provenance else None,
            "causal_chain": dict(order.causal_chain) if order.causal_chain else None,
            "replaces_order_id": order.replaces_order_id,
        }

    @staticmethod
    def from_content(data: dict) -> Order:
        from decimal import Decimal

        order = Order(
            order_id=data["order_id"], client_order_id=data["client_order_id"],
            strategy_id=data["strategy_id"], symbol=data["symbol"],
            side=OrderSide(data["side"]), order_type=OrderType(data["order_type"]),
            quantity=Decimal(data["quantity"]),
            price=Decimal(data["price"]) if data.get("price") else None,
            stop_loss=Decimal(data["stop_loss"]) if data.get("stop_loss") else None,
            take_profit=Decimal(data["take_profit"]) if data.get("take_profit") else None,
            time_in_force=TimeInForce(data["time_in_force"]),
            environment=data["environment"], policy_id=data["policy_id"],
            status=OrderStatus(data["status"]), source=data["source"],
            created_at=parse_canonical(data["created_at"]),
            updated_at=parse_canonical(data["updated_at"]),
            correlation_id=data["correlation_id"],
            risk_decision_id=data.get("risk_decision_id"),
            causation_id=data.get("causation_id"),
            account_id=data.get("account_id"), intent_id=data.get("intent_id"),
            portfolio_decision_id=data.get("portfolio_decision_id"),
            risk_decision_hash=data.get("risk_decision_hash"),
            policy_version=data.get("policy_version"),
            risk_context_hash=data.get("risk_context_hash"),
            idempotency_key=data.get("idempotency_key"),
            expires_at=parse_canonical(data["expires_at"]) if data.get("expires_at") else None,
            order_version=int(data.get("order_version", 1)),
            provenance=data.get("provenance"),
            causal_chain=data.get("causal_chain"),
            replaces_order_id=data.get("replaces_order_id"),
        )
        order.validate()
        return order


class SqliteExecutionReportStore(ExecutionReportStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, report: ExecutionReport) -> bool:
        report.validate()
        existing = self.find_by_idempotency_key(report.idempotency_key)
        if existing is not None:
            return False  # duplicate broker report: no duplicate canonical effect
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO execution_reports (execution_id, order_id, environment,"
                    " idempotency_key, content) VALUES (?,?,?,?,?)",
                    (report.execution_id, report.order_id, report.environment,
                     report.idempotency_key, _dump(report.to_dict())),
                )
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as exc:
            raise StorageError(
                f"Execution report store failure: {exc}",
                location="sqlite.execution_report.append",
            ) from exc
        return True

    def get_by_id(self, execution_id: str) -> ExecutionReport:
        row = self._db.execute(
            "SELECT content FROM execution_reports WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Execution report '{execution_id}' not found",
                location="sqlite.execution_report.get_by_id",
            )
        return self._from_content(json.loads(row[0]))

    def iter_by_order(self, order_id: str) -> Iterator[ExecutionReport]:
        for row in self._db.execute(
            "SELECT content FROM execution_reports WHERE order_id = ? ORDER BY seq",
            (order_id,),
        ):
            yield self._from_content(json.loads(row[0]))

    def find_by_idempotency_key(self, idempotency_key: str) -> ExecutionReport | None:
        row = self._db.execute(
            "SELECT content FROM execution_reports WHERE idempotency_key = ? LIMIT 1",
            (idempotency_key,),
        ).fetchone()
        return None if row is None else self._from_content(json.loads(row[0]))

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM execution_reports").fetchone()[0])

    @staticmethod
    def _from_content(data: dict) -> ExecutionReport:
        from core.execution.contracts import OrderSide

        report = ExecutionReport(
            execution_id=data["execution_id"], order_id=data["order_id"],
            environment=data["environment"],
            execution_type=ExecutionType(data["execution_type"]),
            status=ExecutionStatus(data["status"]),
            symbol=data["symbol"], side=OrderSide(data["side"]),
            executed_quantity=data["executed_quantity"],
            execution_price=data["execution_price"], currency=data["currency"],
            broker_order_id=data.get("broker_order_id"),
            fees=data.get("fees"), commission=data.get("commission"),
            swap=data.get("swap"),
            broker_timestamp=parse_canonical(data["broker_timestamp"]),
            received_at=parse_canonical(data["received_at"]),
            provenance=data["provenance"],
            raw_reference=data["raw_reference"],
            idempotency_key=data["idempotency_key"],
            correction_of=data.get("correction_of"),
            normalized_error=BrokerError(data["normalized_error"])
            if data.get("normalized_error") else None,
        )
        report.validate()
        return report


class SqliteOutboxStore(OutboxStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, message: OutboxMessage) -> None:
        message.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO outbox_messages (message_id, aggregate_id, event_type,"
                    " environment, delivery_status, attempt_count, content)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (message.message_id, message.aggregate_id, message.event_type,
                     message.environment, message.delivery_status.value,
                     message.attempt_count, _dump(self._to_content(message))),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Outbox message {message.message_id} already stored",
                location="sqlite.outbox.append",
            ) from exc

    def mark_delivered(self, message_id: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE outbox_messages SET delivery_status = 'DELIVERED'"
                " WHERE message_id = ?", (message_id,),
            )

    def mark_failed(self, message_id: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE outbox_messages SET delivery_status = 'FAILED'"
                " WHERE message_id = ?", (message_id,),
            )

    def increment_attempt(self, message_id: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE outbox_messages SET attempt_count = attempt_count + 1"
                " WHERE message_id = ?", (message_id,),
            )

    def iter_pending(self) -> Iterator[OutboxMessage]:
        for row in self._db.execute(
            "SELECT content FROM outbox_messages WHERE delivery_status = 'PENDING'"
            " ORDER BY seq"
        ):
            yield self._from_content(json.loads(row[0]))

    def get(self, message_id: str) -> OutboxMessage:
        row = self._db.execute(
            "SELECT content FROM outbox_messages WHERE message_id = ?", (message_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Outbox message '{message_id}' not found", location="sqlite.outbox.get",
            )
        return self._from_content(json.loads(row[0]))

    @staticmethod
    def _to_content(message: OutboxMessage) -> dict:
        return {
            "message_id": message.message_id, "aggregate_id": message.aggregate_id,
            "event_type": message.event_type, "payload": dict(message.payload),
            "payload_hash": message.payload_hash,
            "created_at": canonical(message.created_at),
            "delivery_status": message.delivery_status.value,
            "attempt_count": message.attempt_count, "environment": message.environment,
        }

    @staticmethod
    def _from_content(data: dict) -> OutboxMessage:
        message = OutboxMessage(
            message_id=data["message_id"], aggregate_id=data["aggregate_id"],
            event_type=data["event_type"], payload=data["payload"],
            payload_hash=data["payload_hash"],
            created_at=parse_canonical(data["created_at"]),
            delivery_status=DeliveryStatus(data["delivery_status"]),
            attempt_count=int(data["attempt_count"]), environment=data["environment"],
        )
        message.validate()
        return message
