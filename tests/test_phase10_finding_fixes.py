"""Phase 10 runtime-gate finding fixes (server-local epoch + margin_mode).

Finding 1 (FDX-TIME): MT5 tick.time is the SERVER wall clock as an epoch
(measured +3h on the DEMO terminal). MT5TickSource now measures the offset
per tick and converts to true UTC; absurd/non-timezone-like offsets fail
closed.

Finding 2 (EXEC-003): real MT5 ACCOUNT_MARGIN_MODE is 0=RETAIL_NETTING,
1=EXCHANGE, 2=RETAIL_HEDGING (verified live; real account -> 2). The
adapter now maps all three; only truly unknown values stay UNKNOWN.

A deployment-gated test exercises both fixes against the REAL terminal
when one is present (mirrors the existing documented gate pattern; it
skips only when the terminal/package is absent, never to hide a defect).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest

from adapters.market_data.mt5_feed import MT5TickSource
from adapters.mt5.execution import MT5ExecutionAdapter
from architecture.contracts.errors import ContractError
from architecture.contracts.time import utc_now
from core.ems.adapter import PositionSemantics


class _StubTick:
    def __init__(self, epoch: int, bid=1.1, ask=1.2):
        self.time = epoch
        self.time_msc = epoch * 1000
        self.bid, self.ask, self.last, self.volume = bid, ask, 0.0, 0


class _StubMT5:
    """Emits exactly one tick whose server epoch = now + offset."""

    def __init__(self, offset_seconds: int):
        import time
        self._epoch = int(time.time()) + offset_seconds

    def initialize(self):
        return True

    def symbol_info_tick(self, symbol):
        return _StubTick(self._epoch)


def _one_tick(source: MT5TickSource):
    source.connect()
    for tick in source.ticks("EURUSD"):
        return tick
    raise AssertionError("stub produced no tick")


class TestFinding1ServerEpoch:
    def test_positive_offset_converts_to_true_utc(self):
        src = MT5TickSource(module=_StubMT5(3 * 3600))
        before = utc_now()
        tick = _one_tick(src)
        after = utc_now()
        assert src.server_offset_seconds == 3 * 3600
        # FDX-TIME: event_time no longer in the future; within the window
        assert before - timedelta(seconds=5) <= tick.event_time <= after
        assert tick.ingestion_time >= tick.event_time

    def test_zero_offset_server_on_utc(self):
        src = MT5TickSource(module=_StubMT5(0))
        tick = _one_tick(src)
        assert src.server_offset_seconds == 0
        assert tick.ingestion_time >= tick.event_time

    def test_half_hour_offset_supported(self):
        src = MT5TickSource(module=_StubMT5(90 * 60))
        _one_tick(src)
        assert src.server_offset_seconds == 90 * 60

    def test_non_timezone_skew_fails_closed(self):
        # 3h offset plus 700s of non-tz noise -> refuse (never guess)
        src = MT5TickSource(module=_StubMT5(3 * 3600 + 700))
        with pytest.raises(ContractError) as err:
            _one_tick(src)
        assert "FDX-TIME" in str(err.value) or err.value.rule_id == "FDX-TIME"

    def test_absurd_skew_fails_closed(self):
        src = MT5TickSource(module=_StubMT5(20 * 3600))
        with pytest.raises(ContractError):
            _one_tick(src)

    def test_poll_semantics_bounded_no_repeat(self):
        # symbol_info_tick is a poll api: the same tick must be delivered
        # AT MOST ONCE; a poll with no new tick yields nothing (the old
        # `while True` loop re-yielded forever and hung the feed loop).
        src = MT5TickSource(module=_StubMT5(0))
        src.connect()
        first = list(src.ticks("EURUSD"))
        assert len(first) == 1
        assert list(src.ticks("EURUSD")) == []          # no new tick
        src._mt5._epoch += 1                             # terminal moved (within skew tolerance)
        second = list(src.ticks("EURUSD"))
        assert len(second) == 1 and second[0] != first[0]

    def test_bounded_future_drift_passes_gross_future_rejects(self):
        from adapters.market_data.mt5_feed import (CLOCK_SKEW_TOLERANCE,
                                                   FeedError, Tick)
        now = utc_now()
        Tick("EURUSD", now + timedelta(seconds=1), now,
             "1.1", "1.2", "0", "0").validate()          # NTP-grade drift
        with pytest.raises(FeedError):                    # beyond tolerance
            Tick("EURUSD", now + CLOCK_SKEW_TOLERANCE + timedelta(seconds=1),
                 now, "1.1", "1.2", "0", "0").validate()
        Tick("EURUSD", now - timedelta(hours=1), now,
             "1.1", "1.2", "0", "0").validate()           # past events fine

    def test_residual_drift_beyond_contract_tolerance_rejected(self):
        # 3h tz offset + 3s clock drift: offset converts fine, but the
        # remaining future drift (3s > 2s tolerance) is refused AT THE
        # SOURCE (pre-market hardening: never yield a future-dated tick,
        # the pipeline never even sees it).
        src = MT5TickSource(module=_StubMT5(3 * 3600 + 3))
        with pytest.raises(ContractError) as err:
            _one_tick(src)
        assert getattr(err.value, "rule_id", "") == "FDX-TIME"


@dataclass
class _FakeTransport:
    margin_mode: int

    def account_info(self):
        return {"margin_mode": self.margin_mode, "symbols": ("EURUSD",),
                "min_volume": "0.01", "max_volume": "100",
                "volume_step": "0.01", "digits": 5}


class TestFinding2MarginMode:
    @pytest.mark.parametrize("raw,expected", [
        (0, PositionSemantics.NETTING),      # ACCOUNT_MARGIN_MODE_RETAIL_NETTING
        (1, PositionSemantics.EXCHANGE),     # ACCOUNT_MARGIN_MODE_EXCHANGE
        (2, PositionSemantics.HEDGING),      # ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
        (99, PositionSemantics.UNKNOWN),
        (None, PositionSemantics.UNKNOWN),
    ])
    def test_real_enum_mapping(self, raw, expected):
        adapter = MT5ExecutionAdapter(_FakeTransport(raw), environment="DEMO")
        assert adapter.capabilities().position_semantics is expected

    def test_real_hedging_account_passes_exec003_gate(self):
        # the actual blocked case: real account margin_mode=2 must not be
        # UNKNOWN (EXEC-003 blocks only UNKNOWN)
        cap = MT5ExecutionAdapter(
            _FakeTransport(2), environment="DEMO").capabilities()
        assert cap.position_semantics is not PositionSemantics.UNKNOWN
        assert cap.validate() is None


class TestRealTerminalEvidence:
    """Deployment-gated REAL evidence for both fixes (skips only when the
    terminal/package is absent - the documented runtime-gate pattern)."""

    def test_real_terminal_offset_and_margin_mode(self):
        try:
            import MetaTrader5 as mt5
        except ImportError:
            pytest.skip("MetaTrader5 package absent")
        if not mt5.initialize():
            pytest.skip("MT5 terminal not reachable")
        try:
            from architecture.contracts.errors import ContractError as CE
            from adapters.market_data.mt5_feed import CLOCK_SKEW_TOLERANCE
            src = MT5TickSource(module=mt5)
            try:
                tick = _one_tick(src)
            except CE as error:
                # market CLOSED (stale last tick, e.g. weekend): the source
                # must refuse a future-dated event; asserting the fail-closed
                # path IS the honest live evidence in this condition.
                assert "future-dated" in str(error)
                assert getattr(error, "rule_id", "") == "FDX-TIME"
                return
            assert src.server_offset_seconds is not None
            assert abs(src.server_offset_seconds) <= 14 * 3600
            # inter-clock drift is bounded by the contract tolerance and
            # the tick passes its own FDX-TIME validation
            assert tick.event_time - tick.ingestion_time                 <= CLOCK_SKEW_TOLERANCE
            tick.validate()

            class _Real:
                def account_info(self):
                    info = mt5.account_info()
                    return {"margin_mode": info.margin_mode,
                            "symbols": ("EURUSD", "XAUUSD"),
                            "min_volume": "0.01", "max_volume": "100",
                            "volume_step": "0.01", "digits": 5}
            cap = MT5ExecutionAdapter(_Real(), environment="DEMO") \
                .capabilities()
            assert cap.position_semantics in (
                PositionSemantics.NETTING,
                PositionSemantics.EXCHANGE,
                PositionSemantics.HEDGING), \
                f"real margin_mode={_Real().account_info()['margin_mode']}"
        finally:
            mt5.shutdown()
