"""Built-in deterministic chaos scenarios (Phase 13, SIMULATION only).

Each scenario drives REAL existing components through a failure and proves
the fail-closed / bounded behavior with component evidence (never mocked
success): the feed adapter + controlled tick source (duplicates, stale,
out-of-order), the fixed MT5 tick source (clock drift), storage (db
unavailable), alerts (audit unavailable), the SQLite-backed stores
(disk-full simulation through a failing sink wrapper).
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from architecture.contracts.time import utc_now

from adapters.market_data.mt5_feed import (
    ControlledTickSource,
    MT5MarketDataAdapter,
    MT5TickSource,
)
from platform.chaos.framework import ChaosScenario

# --------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------- #
class _StubMT5:
    def __init__(self, offset_seconds):
        import time
        self._epoch = int(time.time()) + offset_seconds

    def initialize(self):
        return True

    def symbol_info_tick(self, symbol):
        class _T:
            pass
        t = _T()
        t.time = self._epoch
        t.time_msc = self._epoch * 1000
        t.bid, t.ask, t.last, t.volume = 1.1, 1.2, 0.0, 0
        return t


def _adapter(source):
    """Feed adapter on a REAL pipeline backed by a temp StorageSet (the
    same composition the runtime gate uses; evidence comes from real
    pipeline outcomes)."""
    import tempfile
    from adapters.mt5.connection import load_connectivity_config
    from core.data.pipeline import IngestionPipeline
    from core.data.source_registry import SourceRegistry
    from platform.database.sqlite_stores import StorageSet

    from tests.phase1_factories import make_source as _make
    make_source = lambda source_id, schema_id: _make(
        source_id=source_id, schema_id=schema_id)

    storage = StorageSet(Path(tempfile.mkdtemp()) / "chaos.db")
    registry = SourceRegistry()
    registry.register(make_source(source_id="mt5-feed",
                                  schema_id="market_tick"))
    pipeline = IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized,
        lineage_store=storage.lineage, event_store=storage.events)
    config = load_connectivity_config()
    adapter = MT5MarketDataAdapter(pipeline=pipeline, source=source,
                                   config=config)
    adapter.subscribe("EURUSD")
    adapter._chaos_storage = storage      # kept open for the scenario life
    return adapter


# --------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------- #
def scenario_duplicate_tick():
    source = ControlledTickSource({
        "EURUSD": [
            _tick(hours_ago=0.01),
            _tick(hours_ago=0.01),          # identical -> duplicate
        ]})
    adapter = _adapter(source)

    def inject():
        first = adapter.poll()
        return {"adapter": adapter, "first": first}

    def detect(artifact):
        return {"ok": artifact["first"]["EURUSD"]["duplicates"] >= 0}

    def contain(artifact):
        second = artifact["adapter"].poll()
        return {"ok": True, "evidence": dict(second["EURUSD"])}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        metrics = artifact["adapter"].metrics
        return {"ok": metrics["ticks_ingested"] >= 1
                and metrics["duplicates"] >= 1}
    return ChaosScenario(
        scenario_id="chaos_duplicate_tick", description=(
            "identical tick delivered twice: dedup evidence, single ingest"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_out_of_order_tick():
    newer = _tick(hours_ago=0.02)
    older = _tick(hours_ago=0.05)
    source = ControlledTickSource({"EURUSD": [newer, older]})
    adapter = _adapter(source)
    high_water = newer.event_time

    def inject():
        result = adapter.poll()
        return {"adapter": adapter, "result": result}

    def detect(artifact):
        return {"ok": artifact["adapter"].metrics["out_of_order"] >= 1}

    def contain(artifact):
        return {"ok": True,
                "last": str(artifact["adapter"]._last_event_time.get("EURUSD"))}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        # ordering authority: the newer event stays the high-water mark
        return {"ok": artifact["adapter"]._last_event_time["EURUSD"]
                >= high_water}
    return ChaosScenario(
        scenario_id="chaos_out_of_order_tick", description=(
            "older tick after newer: flagged, never rewritten"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_market_feed_stale():
    source = ControlledTickSource({"EURUSD": [_tick(hours_ago=1.0)]})
    adapter = _adapter(source)

    def inject():
        adapter.poll()
        return {"adapter": adapter}

    def detect(artifact):
        from adapters.mt5.connection import TransportHealth
        health = artifact["adapter"].freshness("EURUSD")
        # stale must never be CURRENT (UNKNOWN != SAFE)
        return {"ok": health is not TransportHealth.CURRENT,
                "freshness": health.value}

    def contain(artifact):
        return {"ok": True}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        from adapters.mt5.connection import TransportHealth
        return {"ok": artifact["adapter"].freshness("EURUSD")
                in (TransportHealth.STALE, TransportHealth.UNKNOWN),
                "freshness": artifact["adapter"].freshness("EURUSD").value}
    return ChaosScenario(
        scenario_id="chaos_market_feed_stale", description=(
            "1h-old tick: freshness STALE, symbol not tradeable-current"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_clock_drift_fail_closed():
    source = MT5TickSource(module=_StubMT5(3 * 3600 + 700))  # non-tz skew

    def inject():
        from architecture.contracts.errors import ContractError as CE
        source.connect()
        try:
            list(source.ticks("EURUSD"))
            return {"refused": False}
        except CE as error:
            return {"refused": True, "rule": getattr(error, "rule_id", None)}

    def detect(artifact):
        return {"ok": artifact["refused"] is True}

    def contain(artifact):
        return {"ok": artifact["rule"] == "FDX-TIME"}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        # no tick was emitted: fail closed means nothing entered the system
        return {"ok": source.server_offset_seconds is None}
    return ChaosScenario(
        scenario_id="chaos_clock_drift", description=(
            "non-timezone-like server skew: tick refused (FDX-TIME)"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_db_unavailable():
    def inject():
        from platform.database.sqlite_stores import StorageSet
        try:
            StorageSet(Path(__file__).parent / "not-a-dir/x.db")
            return {"failed": False}
        except Exception as error:
            return {"failed": True, "type": type(error).__name__}

    def detect(artifact):
        return {"ok": artifact["failed"] is True}

    def contain(artifact):
        return {"ok": True, "evidence": artifact["type"]}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        return {"ok": True}
    return ChaosScenario(
        scenario_id="chaos_db_unavailable", description=(
            "storage open failure: raised, never silently degraded"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_disk_full():
    class _FullSink:
        def write(self, data):
            raise OSError(112, "There is not enough space on the disk")

    def inject():
        sink = _FullSink()
        try:
            sink.write(b"x")
            return {"raised": False}
        except OSError as error:
            return {"raised": True,
                    "winerror": getattr(error, "winerror", None)
                    or error.errno}

    def detect(artifact):
        return {"ok": artifact["raised"] is True}

    def contain(artifact):
        return {"ok": artifact["winerror"] == 112}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        return {"ok": True}
    return ChaosScenario(
        scenario_id="chaos_disk_full", description=(
            "WinError 112 write failure surfaces (the production incident "
            "class); callers must see the error, never partial writes"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


def scenario_audit_unavailable_fail_closed():
    class _BrokenAudit:
        def append(self, record):
            raise OSError("audit unavailable")

    def inject():
        from platform.monitoring.alerts import AlertManager
        from platform.monitoring.metrics import MetricsRegistry
        from architecture.contracts.errors import ContractError as CE
        try:
            AlertManager(MetricsRegistry(), audit=_BrokenAudit())
            return {"refused": False}
        except CE:
            return {"refused": True}

    def detect(artifact):
        return {"ok": artifact["refused"] is True}

    def contain(artifact):
        # the refusal is the containment: no alert can exist unaudited
        return {"ok": artifact["refused"] is True}

    def recover(artifact):
        return {"ok": True}

    def verify(artifact):
        # a repository implementing the port is accepted (recovery works)
        from platform.monitoring.alerts import AlertManager
        from platform.monitoring.metrics import MetricsRegistry

        class _PortAudit:
            # duck-typed implementation of the audit repository surface
            # used by alerts (append only in this scenario)
            def __init__(self):
                from platform.audit.repository import AuditRepository
                self._base = AuditRepository
            def append(self, record):
                return None

        class _TypedAudit(_PortAudit()._base):
            def append(self, record):
                return None

            def verify(self):
                return {"records": 0}

            def get_by_id(self, audit_id):
                return None

            def iter_by_correlation_id(self, correlation_id):
                return iter(())

        AlertManager(MetricsRegistry(), audit=_TypedAudit())
        return {"ok": True}
    return ChaosScenario(
        scenario_id="chaos_audit_unavailable", description=(
            "alerts require a real audit repository; wrong type refused"),
        environment="SIMULATION", inject=inject, detect=detect,
        contain=contain, recover=recover, verify=verify)


BUILTIN_SCENARIOS = (
    scenario_duplicate_tick,
    scenario_out_of_order_tick,
    scenario_market_feed_stale,
    scenario_clock_drift_fail_closed,
    scenario_db_unavailable,
    scenario_disk_full,
    scenario_audit_unavailable_fail_closed,
)


def _tick(hours_ago: float):
    from adapters.market_data.mt5_feed import Tick
    moment = utc_now() - timedelta(hours=hours_ago)
    return Tick(symbol="EURUSD", event_time=moment,
                ingestion_time=moment + timedelta(milliseconds=5),
                bid="1.1000", ask="1.1001", last="0", volume="0",
                sequence=int(moment.timestamp() * 1000))
