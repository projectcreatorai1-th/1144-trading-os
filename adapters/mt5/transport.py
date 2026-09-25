"""MT5 terminal transport port (adapters.mt5).

The transport is the ONLY place the MetaTrader5 terminal API is touched.
`MetaTrader5Transport` activates when the `MetaTrader5` package and a
terminal are present at deployment; it performs no domain logic. Tests and
simulated-demo deployments inject a controlled transport implementing this
port (deterministic scripted terminal responses)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

from core.ems.adapter import ConnectionState

try:  # pragma: no cover - deployment-dependent optional dependency
    import MetaTrader5 as _mt5  # type: ignore
    MT5_PACKAGE_AVAILABLE = True
except ImportError:
    _mt5 = None
    MT5_PACKAGE_AVAILABLE = False


@dataclass(frozen=True)
class MT5TransportResult:
    ok: bool
    raw: Mapping[str, Any] = field(default_factory=dict)


class MT5Transport(ABC):
    """Terminal boundary: connect/disconnect, order_send/cancel/get,
    account_info, connection_state."""

    @abstractmethod
    def connect(self) -> None:  # pragma: no cover - port
        ...

    @abstractmethod
    def disconnect(self) -> None:  # pragma: no cover
        ...

    @abstractmethod
    def connection_state(self) -> ConnectionState:  # pragma: no cover
        ...

    @abstractmethod
    def account_info(self) -> Mapping[str, Any]:  # pragma: no cover
        ...

    @abstractmethod
    def order_send(self, mt5_request: Mapping[str, Any]) -> MT5TransportResult:  # pragma: no cover
        ...

    @abstractmethod
    def order_cancel(self, params: Mapping[str, Any]) -> MT5TransportResult:  # pragma: no cover
        ...

    @abstractmethod
    def order_get(self, params: Mapping[str, Any]) -> MT5TransportResult:  # pragma: no cover
        ...


class MetaTrader5Transport(MT5Transport):
    """Real terminal transport. Requires the MetaTrader5 package AND a running
    terminal with credentials supplied through the deployment secret store -
    credentials never live in source or logs (SECTION 45/46)."""

    def __init__(self) -> None:
        if not MT5_PACKAGE_AVAILABLE:
            raise RuntimeError(
                "MetaTrader5 package not installed; terminal transport unavailable. "
                "Deploy with the package + terminal or inject a controlled transport."
            )
        self._state = ConnectionState.DISCONNECTED

    def connect(self) -> None:
        if not _mt5.initialize():
            self._state = ConnectionState.DISCONNECTED
            raise ConnectionError("MT5 terminal initialize failed")
        self._state = ConnectionState.CONNECTED

    def disconnect(self) -> None:
        _mt5.shutdown()
        self._state = ConnectionState.DISCONNECTED

    def connection_state(self) -> ConnectionState:
        return self._state

    def account_info(self) -> Mapping[str, Any]:
        info = _mt5.account_info()
        if info is None:
            return {"margin_mode": None, "symbols": ("*",)}
        symbols = tuple(
            s.name for s in (_mt5.symbols_get() or [])[:200]
        ) or ("*",)
        return {
            "login": info.login,
            "margin_mode": getattr(info, "margin_mode", None),
            "min_volume": getattr(info, "trade_min", None) or "0.01",
            "max_volume": getattr(info, "trade_max", None) or "1000",
            "volume_step": getattr(info, "volume_step", None) or "0.01",
            "digits": getattr(info, "trade_digits", None) or 5,
            "symbols": symbols,
        }

    def order_send(self, mt5_request: Mapping[str, Any]) -> MT5TransportResult:
        result = _mt5.order_send(dict(mt5_request))
        if result is None:
            return MT5TransportResult(False, {"retcode": -1, "comment": "no result"})
        ok = result.retcode == 10009  # TRADE_RETCODE_DONE
        return MT5TransportResult(ok, {
            "retcode": result.retcode, "comment": result.comment,
            "ticket": getattr(result, "order", None),
            "reference": f"MT5-{getattr(result, 'order', 'NA')}",
        })

    def order_cancel(self, params: Mapping[str, Any]) -> MT5TransportResult:
        request = {"action": _mt5.TRADE_ACTION_REMOVE, "order": int(params["ticket"])}
        result = _mt5.order_send(request)
        if result is None:
            return MT5TransportResult(False, {"retcode": -1})
        return MT5TransportResult(result.retcode == 10009,
                                  {"retcode": result.retcode, "ticket": params["ticket"]})

    def order_get(self, params: Mapping[str, Any]) -> MT5TransportResult:
        orders = _mt5.orders_get(ticket=int(params["ticket"]))
        if not orders:
            history = _mt5.history_orders_get(ticket=int(params["ticket"]))
            orders = history or ()
        if not orders:
            return MT5TransportResult(False, {"retcode": "NOT_FOUND", "ticket": params["ticket"]})
        order = orders[0]
        return MT5TransportResult(True, {
            "retcode": "OK", "ticket": params["ticket"],
            "state": getattr(order, "state", None),
            "volume_current": getattr(order, "volume_current", None),
        })
