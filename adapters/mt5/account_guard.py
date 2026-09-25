"""Account/environment guard (adapters.mt5, pre-market Phase 11).

The runtime identity of the connected terminal (environment/trade mode,
account login, server, symbols) must MATCH the expected identity before
any execution path may proceed. Mismatch is a HARD BLOCK — never a
warning (UNKNOWN ≠ SAFE; DEMO ≠ LIVE ≠ wrong account).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from architecture.contracts.errors import ContractError

CONTRACT_VERSION = "1.0.0"
RULE_ID = "MT5X-ACCOUNT-GUARD"


class AccountGuardError(ContractError):
    rule_id = RULE_ID


#: terminal runtime identity snapshot (from MetaTrader5 account_info /
#: terminal_info / symbol_info — all observable, no secrets)
@dataclass(frozen=True)
class TerminalIdentity:
    trade_mode: int              # 0=DEMO 1=CONTEST 2=REAL
    login: int
    server: str
    symbols_available: tuple[str, ...] = ()

    @staticmethod
    def from_mt5(account: Any, symbols: Any = None) -> "TerminalIdentity":
        return TerminalIdentity(
            trade_mode=int(account.trade_mode),
            login=int(account.login),
            server=str(account.server),
            symbols_available=tuple(str(s) for s in (symbols or ())))

    def as_dict(self) -> dict:
        return {"trade_mode": self.trade_mode, "login": self.login,
                "server": self.server,
                "symbols": list(self.symbols_available)}


#: expected identity — set from the DEMO session the operator pinned
@dataclass(frozen=True)
class ExpectedIdentity:
    trade_mode: int = 0          # DEMO only in this deployment
    login: int | None = None     # None = not pinned yet (fail-closed on
                                 # the other dimensions; pinning still
                                 # recommended before live E2E)
    server: str | None = None
    symbols: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.trade_mode != 0:
            raise AccountGuardError(
                "expected identity must be DEMO (trade_mode=0); LIVE is "
                "structurally refused and cannot be pinned here",
                location="account_guard.expected", rule_id=RULE_ID)


#: session-market clock contract (pre-market Phase 13): trading sessions
#: evaluated on an injectable clock so weekend/rollover/close are
#: verifiable without waiting for the market.
@dataclass(frozen=True)
class SessionWindow:
    name: str
    open_hour_utc: int
    close_hour_utc: int
    days_utc: tuple[int, ...]    # 0=Mon .. 6=Sun

    def is_open(self, moment) -> bool:
        if moment.weekday() not in self.days_utc:
            return False
        hour = moment.hour
        if self.open_hour_utc <= self.close_hour_utc:
            return self.open_hour_utc <= hour < self.close_hour_utc
        return hour >= self.open_hour_utc or hour < self.close_hour_utc


FOREX_LONDON_NY = SessionWindow("forex-london-ny", 7, 21,
                                days_utc=(0, 1, 2, 3, 4))


class SessionGuard:
    """Market-closed => no order flow (deterministic; clock injectable)."""

    def __init__(self, windows: tuple[SessionWindow, ...] =
                 (FOREX_LONDON_NY,)) -> None:
        self._windows = windows
        self._overridden_open: bool | None = None

    def is_open(self, moment) -> bool:
        if self._overridden_open is not None:
            return self._overridden_open
        return any(w.is_open(moment) for w in self._windows)

    def override_for_tests(self, open_: bool | None) -> None:
        self._overridden_open = open_


class AccountGuard:
    """Hard identity gate: expected vs observed terminal runtime."""

    def __init__(self, expected: ExpectedIdentity) -> None:
        expected.validate()
        self._expected = expected
        self.last_decision: dict = {}

    def check(self, observed: TerminalIdentity,
              session: SessionGuard | None = None,
              moment=None) -> dict:
        reasons: list[str] = []
        if observed.trade_mode != self._expected.trade_mode:
            reasons.append(
                f"trade_mode {observed.trade_mode} != expected "
                f"{self._expected.trade_mode} "
                f"(0=DEMO,1=CONTEST,2=REAL)")
        if self._expected.login is not None \
                and observed.login != self._expected.login:
            reasons.append(
                f"login {observed.login} != pinned {self._expected.login}")
        if self._expected.server is not None \
                and observed.server != self._expected.server:
            reasons.append(
                f"server {observed.server!r} != pinned "
                f"{self._expected.server!r}")
        for symbol in self._expected.symbols:
            if symbol not in observed.symbols_available:
                reasons.append(f"symbol {symbol!r} unavailable")
        if session is not None and moment is not None \
                and not session.is_open(moment):
            reasons.append("market session CLOSED (no order flow)")
        decision = {"allowed": not reasons, "reasons": reasons,
                    "observed": observed.as_dict(),
                    "expected": {"trade_mode": self._expected.trade_mode,
                                 "login": self._expected.login,
                                 "server": self._expected.server}}
        self.last_decision = decision
        if reasons:
            raise AccountGuardError(
                "ACCOUNT GUARD HARD BLOCK: " + "; ".join(reasons),
                location="account_guard.check", rule_id=RULE_ID,
                details=decision)
        return decision
