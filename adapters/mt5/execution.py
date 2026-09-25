"""MT5 execution adapter (adapters.mt5).

Implements core.ems.ExecutionAdapter with a strict mapping layer:
Canonical Order -> MT5 request dict, and MT5 response -> normalized
AdapterResponse. MT5-specific objects NEVER leak into the domain (SECTION 18).
The terminal transport is a port (MT5Transport); the MetaTrader5-backed
transport activates only when the package + terminal are present at
deployment - this module never fabricates transport results."""
from __future__ import annotations

import itertools
from typing import Any, Mapping

from adapters.mt5.transport import MT5Transport, MT5TransportResult
from core.ems.adapter import (
    AdapterCapability,
    AdapterResponse,
    ConnectionState,
    ExecutionAdapter,
    PositionSemantics,
)
from core.oms.contracts import normalize_error

ADAPTER_VERSION = "1.0.0"

#: MT5 order type mapping (canonical -> MT5 code)
ORDER_TYPE_MAP = {"MARKET": 0, "LIMIT": 1, "STOP": 3, "STOP_LIMIT": 4}
#: MT5 time-in-force mapping
TIF_MAP = {"GTC": 0, "IOC": 1, "FOK": 2, "DAY": 3}


class MT5ExecutionAdapter(ExecutionAdapter):
    def __init__(self, transport: MT5Transport, *, environment: str = "DEMO") -> None:
        if environment not in ("DEMO", "LIVE"):
            raise ValueError(
                "MT5 adapter binds to DEMO or LIVE only (explicit environment, "
                "never a default)"
            )
        self._transport = transport
        self._environment = environment
        self._sequence = itertools.count(1)

    def adapter_id(self) -> str:
        return f"mt5-{self._environment.lower()}"

    def environment(self) -> str:
        return self._environment

    def connect(self) -> None:
        self._transport.connect()

    def disconnect(self) -> None:
        self._transport.disconnect()

    def health(self) -> ConnectionState:
        return self._transport.connection_state()

    def capabilities(self) -> AdapterCapability:
        account = self._transport.account_info()
        # Real MT5 ACCOUNT_MARGIN_MODE values (verified live against the
        # DEMO terminal 2026-09-25, account 411173797 -> margin_mode=2):
        # 0=ACCOUNT_MARGIN_MODE_RETAIL_NETTING, 1=ACCOUNT_MARGIN_MODE_EXCHANGE,
        # 2=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING. Phase 10 finding 2: the old
        # mapping (1->HEDGING, else UNKNOWN) mislabelled real hedging
        # accounts and blocked execution via EXEC-003.
        semantics = {
            0: PositionSemantics.NETTING,
            1: PositionSemantics.EXCHANGE,
            2: PositionSemantics.HEDGING,
        }.get(account.get("margin_mode"), PositionSemantics.UNKNOWN)
        return AdapterCapability(
            adapter_id=self.adapter_id(), adapter_version=ADAPTER_VERSION,
            environment=self._environment,
            supported_symbols=tuple(account.get("symbols", ("*",))),
            order_types=("MARKET", "LIMIT", "STOP", "STOP_LIMIT"),
            time_in_force=("GTC", "IOC", "FOK", "DAY"),
            min_quantity=str(account.get("min_volume", "0.01")),
            max_quantity=str(account.get("max_volume", "1000")),
            quantity_step=str(account.get("volume_step", "0.01")),
            price_precision=int(account.get("digits", 5)),
            position_semantics=semantics,
            partial_fill_support=bool(account.get("filling_partial", True)),
            cancel_support=True, replace_support=True,
            provenance={"source": "mt5-terminal", "observed": True,
                        "adapter_version": ADAPTER_VERSION},
        )

    # ------------------------------------------------------------------ #
    # Canonical -> MT5 request mapping                                    #
    # ------------------------------------------------------------------ #
    def _mt5_request(self, canonical: Mapping[str, Any]) -> dict[str, Any]:
        request: dict[str, Any] = {
            "action": ORDER_TYPE_MAP.get(str(canonical["order_type"]).upper(), 0),
            "symbol": canonical["symbol"],
            "volume": float(canonical["quantity"]),  # wire format only; canonical stays decimal
            "type": 0 if str(canonical["side"]).upper() == "BUY" else 1,
            "type_filling": TIF_MAP.get(str(canonical["time_in_force"]).upper(), 0),
            "comment": canonical["idempotency_key"] or canonical["order_id"],
        }
        price = canonical.get("price")
        if price is not None:
            request["price"] = float(price)  # wire format only
        return request

    def submit_order(self, canonical_request: Mapping[str, Any]) -> AdapterResponse:
        mt5_request = self._mt5_request(canonical_request)
        result = self._transport.order_send(mt5_request)
        return self._to_response(canonical_request, result, action="submit")

    def cancel_order(self, broker_order_id: str) -> AdapterResponse:
        result = self._transport.order_cancel({"ticket": int(broker_order_id)})
        return self._to_response({"action": "cancel"}, result, action="cancel")

    def replace_order(self, broker_order_id: str,
                      replacement: Mapping[str, Any]) -> AdapterResponse:
        # MT5 has no atomic replace: cancel + new submit, tracked by the OMS chain
        cancel_result = self._transport.order_cancel({"ticket": int(broker_order_id)})
        if not cancel_result.ok:
            return self._to_response(replacement, cancel_result, action="replace-cancel")
        mt5_request = self._mt5_request(replacement)
        result = self._transport.order_send(mt5_request)
        return self._to_response(replacement, result, action="replace-submit")

    def poll_order(self, broker_order_id: str) -> AdapterResponse:
        result = self._transport.order_get({"ticket": int(broker_order_id)})
        return self._to_response({"action": "poll"}, result, action="poll")

    # ------------------------------------------------------------------ #
    # MT5 response -> normalized AdapterResponse (raw preserved)          #
    # ------------------------------------------------------------------ #
    def _to_response(self, request: Mapping[str, Any], result: MT5TransportResult,
                     *, action: str) -> AdapterResponse:
        raw = {"action": action, "request": dict(request), "mt5_result": result.raw}
        reference = str(result.raw.get("reference") or f"MT5-{next(self._sequence):08d}")
        error_code = result.raw.get("retcode")
        normalized = normalize_error(
            error_code if not result.ok else None,
            adapter=self.adapter_id(), adapter_version=ADAPTER_VERSION,
        ) if not result.ok else None
        return AdapterResponse(
            ok=result.ok,
            broker_order_id=str(result.raw["ticket"]) if result.ok and "ticket" in result.raw else None,
            normalized_error=normalized.value if normalized else None,
            raw=raw,
            raw_reference=reference,
        )
