"""Shared canonical execution type markers (owned by core.execution).

Adapters import canonical contract types through this module - adapters
implement core types; they never define their own order semantics."""
from __future__ import annotations

from core.execution.contracts import (  # re-export: the canonical types
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

__all__ = ["Order", "OrderSide", "OrderStatus", "OrderType", "TimeInForce",
           "CanonicalOrderTypes"]

#: Marker for adapter implementations consuming canonical order types.
CanonicalOrderTypes = True
