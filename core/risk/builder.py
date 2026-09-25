"""Risk context builder (owned by core.risk).

Builds the canonical RiskContext from the Phase 2 state store (account
balances, data quality, system state) plus explicit overrides. Anything not
derivable is left UNKNOWN (None) - never guessed. Point-in-time: uses
`at` only; never reads a clock (SECTIONS 8/20)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from architecture.contracts.errors import ContractValidationError
from architecture.contracts.time import ensure_utc
from core.risk.context import RiskContext, build_context
from core.state.contracts import StateCategory
from core.state.stores import StateStore

CONTRACT_VERSION = "1.0.0"


class RiskContextBuilder:
    def __init__(self, states: StateStore) -> None:
        self._states = states

    def build(
        self,
        *,
        account_id: str | None,
        environment: str,
        at: datetime,
        overrides: Mapping[str, Any] | None = None,
        system_entity: str = "1144-os",
        data_source_entity: str | None = None,
    ) -> RiskContext:
        moment = ensure_utc(at, location="context.at")
        values: dict[str, Any] = dict(overrides or {})

        if account_id is not None and "account_balance" not in values:
            balances = self._account_balances(account_id)
            if balances is not None:
                currency = values.get("account_currency", "USD")
                if currency in balances:
                    values.setdefault("account_balance", balances[currency])

        if "data_quality" not in values and data_source_entity is not None:
            quality = self._status_of(StateCategory.DATA_STATE, data_source_entity)
            if quality is not None:
                values.setdefault("data_quality", quality)

        if "system_state" not in values:
            system = self._status_of(StateCategory.SYSTEM_STATE, system_entity)
            if system is not None:
                values.setdefault("system_state", system)

        return build_context(as_of=moment, environment=environment, **values)

    def _account_balances(self, account_id: str) -> Mapping[str, str] | None:
        state = self._states.get_current_state(StateCategory.ACCOUNT_STATE.value, account_id)
        if state is None:
            return None
        balances = state.payload.get("balances")
        return balances if isinstance(balances, Mapping) else None

    def _status_of(self, category: StateCategory, entity_id: str) -> str | None:
        state = self._states.get_current_state(category.value, entity_id)
        return state.status if state is not None else None


def require_known(value: Any, field_name: str) -> Any:
    """Helper for callers: explicit UNKNOWN propagation."""
    if value is None or value == "UNKNOWN":
        raise ContractValidationError(
            f"{field_name} is UNKNOWN and required (fail closed)",
            location=f"risk.builder.{field_name}",
        )
    return value
