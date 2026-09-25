"""Phase 10 invariants + E2E (golden path on controlled ticks = clearly
labeled SYNTHETIC; runtime gate honesty is itself tested)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from architecture.contracts.errors import ContractError
from architecture.contracts.time import utc_now

from adapters.market_data.mt5_feed import ControlledTickSource, Tick
from adapters.mt5.connection import TransportHealth, load_connectivity_config
from adapters.mt5.reconciliation import BrokerSnapshot

from core.data.pipeline import IngestionPipeline
from core.data.source_registry import SourceRegistry
from core.reconciliation.engine import ReconciliationEngine

from platform.api.connectivity_plane import ConnectivityPlane
from platform.database.sqlite_stores import StorageSet


def _tick(seconds_ago=0, seq=None):
    from architecture.contracts.time import utc_now
    event_time = utc_now().replace(microsecond=0) - \
        timedelta(seconds=seconds_ago)
    return Tick(symbol="EURUSD", event_time=event_time,
                ingestion_time=event_time + timedelta(milliseconds=50),
                bid="1.0850", ask="1.0851", last="1.0850", volume="100",
                sequence=seq or int(event_time.timestamp() * 1000))


@pytest.fixture()
def storage(tmp_path):
    store = StorageSet(tmp_path / "p10-e2e.db")
    yield store
    store.close()


@pytest.fixture()
def pipeline(storage):
    from tests.phase1_factories import make_source
    registry = SourceRegistry()
    registry.register(make_source(source_id="mt5-feed",
                                  schema_id="market_tick"))
    return IngestionPipeline(
        source_registry=registry, raw_store=storage.raw,
        normalized_store=storage.normalized,
        lineage_store=storage.lineage, event_store=storage.events)


@pytest.fixture()
def plane(storage, pipeline):
    ticks = [_tick(seconds_ago=s) for s in (5, 4, 3, 2, 1)]
    instance = ConnectivityPlane(
        pipeline=pipeline,
        reconciliation_engine=ReconciliationEngine(
            storage.reconciliations, storage.audit),
        source=ControlledTickSource({"EURUSD": ticks}),
        controlled=True)
    instance.start()
    return instance


class TestInvariants:
    def test_unknown_ne_safe_feed(self, plane):
        plane.monitor.transition("STALE", reason="gap")
        plane.monitor.transition("RECONNECTING", reason="policy")
        plane.monitor.transition("UNKNOWN", reason="uncertain")
        assert plane.freshness("EURUSD") == TransportHealth.UNKNOWN.value

    def test_disconnected_ne_safe(self, plane):
        plane.monitor.transition("STALE", reason="gap")
        plane.monitor.transition("DISCONNECTED", reason="lost")
        assert plane.freshness("EURUSD") == \
            TransportHealth.DISCONNECTED.value

    def test_missing_tick_ne_current(self, plane):
        # never polled XAUUSD -> no tick seen -> UNKNOWN, not CURRENT
        assert plane.freshness("XAUUSD") == TransportHealth.UNKNOWN.value

    def test_live_refused_everywhere(self, storage, pipeline):
        with pytest.raises(ContractError):
            ConnectivityPlane(
                pipeline=pipeline,
                reconciliation_engine=ReconciliationEngine(
                    storage.reconciliations, storage.audit),
                source=None, controlled=False,
                config=_live_refusing_config())
        from adapters.mt5.connection import ConnectionMonitor
        with pytest.raises(ContractError):
            ConnectionMonitor(environment="LIVE", provider="MT5")

    def test_no_second_pipeline(self, plane):
        import inspect
        from adapters.market_data import mt5_feed
        source = inspect.getsource(mt5_feed)
        assert "event_store" not in source
        assert "raw_store" not in source
        assert "normalized_store" not in source

    def test_controlled_never_claimed_real(self, plane):
        assert plane.status()["source_real"] is False

    def test_every_transition_audited(self, plane):
        events = []
        plane.monitor._on_event = events.append
        plane.monitor.transition("STALE", reason="x")
        assert any(e["event_type"] == "CONNECTION_STATE_CHANGED"
                   for e in events)

    def test_ticks_preserve_all_timestamps(self, plane):
        plane.poll()
        dataset = plane.flush_history()
        observation = dataset.observations[0]
        assert observation.event_time <= observation.available_time
        assert observation.payload["provider"] == "MT5"
        assert "ingestion_time" in observation.payload


def _live_refusing_config():
    from dataclasses import replace
    config = load_connectivity_config()
    live = replace(config, environment="LIVE",
                   adapter_environment="LIVE", content_hash="")
    object.__setattr__(live, "content_hash", live.compute_content_hash())
    return live


class TestGoldenPathE2E:
    def test_full_flow(self, storage, pipeline, plane):
        # feed -> pipeline -> freshness -> history -> reconcile -> status
        result = plane.poll()["EURUSD"]
        assert result["accepted"] == 5
        freshness = plane.freshness("EURUSD")
        assert freshness in ("CURRENT", "STALE")
        dataset = plane.flush_history()
        assert len(dataset.observations) == 5
        assert dataset.data_source == "MT5"  # provider identified
        now = utc_now()
        snapshot = BrokerSnapshot(
            account_login="12345", balance="100000", equity="100000",
            positions={"EURUSD": "0.1"}, observed_at=now,
            environment="DEMO")
        recon = plane.reconcile(snapshot=snapshot,
                                internal_positions={"EURUSD": "0.1"},
                                internal_equity="100000")
        assert recon["positions"]["status"] == "MATCH"
        assert recon["account"]["status"] == "MATCH"
        status = plane.status()
        assert status["connection_state"] == "CONNECTED"
        assert status["reconciliation_metrics"]["mismatches"] == 0
        # reconnect path must land back CONNECTED after evidence

    def test_disconnect_reconnect_resync(self, plane):
        plane.monitor.transition("STALE", reason="gap")
        plane.monitor.transition("DISCONNECTED", reason="lost")
        assert plane.freshness("EURUSD") == \
            TransportHealth.DISCONNECTED.value
        result = plane.reconnect_cycle(reason="recovered")
        assert result["state"] == "CONNECTED" and result["resynced"]

    def test_mismatch_produces_attention_evidence(self, plane):
        plane.poll()
        now = utc_now()
        snapshot = BrokerSnapshot(
            "12345", "100000", "100000", {"XAUUSD": "2.0"}, now, "DEMO")
        recon = plane.reconcile(snapshot=snapshot,
                                internal_positions={"EURUSD": "0.1"},
                                internal_equity="100000")
        assert recon["positions"]["status"] != "MATCH"
        assert plane.status()["reconciliation_metrics"]["mismatches"] >= 1

    def test_runtime_gate_honesty(self, storage, pipeline):
        """Real mode without MetaTrader5: BLOCKED, honestly reported,
        never fabricated as CONNECTED."""
        plane = ConnectivityPlane(
            pipeline=pipeline,
            reconciliation_engine=ReconciliationEngine(
                storage.reconciliations, storage.audit),
            source=None, controlled=False)
        if plane.deployment_blocked:
            assert plane.source is None
            start = plane.start()
            assert start["state"] == "DEPLOYMENT_BLOCKED"
            assert start["real"] is False
            assert plane.status()["source_real"] is False
        else:  # pragma: no cover - deployment with the package present
            assert plane.status()["source_real"] is True
