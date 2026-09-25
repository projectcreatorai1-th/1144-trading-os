"""Provenance and causal trace tests (SECTIONS 23-24) - including failure tests."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from architecture.contracts.causality import validate_causal_chain
from architecture.contracts.errors import CausalityError, ProvenanceError, TimeValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.provenance import Provenance
from tests.factories import at, make_event


@dataclass
class ChainNode:
    event_id: str
    correlation_id: str
    causation_id: str | None


def _chain():
    correlation = new_identifier("correlation_id")
    news = ChainNode(new_identifier("event_id"), correlation, None)
    analysis = ChainNode(new_identifier("event_id"), correlation, news.event_id)
    signal = ChainNode(new_identifier("event_id"), correlation, analysis.event_id)
    order = ChainNode(new_identifier("order_id").replace("ord", "evt"), correlation, signal.event_id)
    return [news, analysis, signal, order]


class TestProvenance:
    def test_valid_provenance(self):
        provenance = Provenance(
            source="adapters.market_data",
            source_id="feed-1",
            ingestion_time=at(12, 0),
            event_time=at(11, 58),
        )
        provenance.validate()

    def test_ingestion_before_event_rejected(self):
        provenance = Provenance(
            source="s", source_id="x",
            ingestion_time=at(11, 0), event_time=at(12, 0),
        )
        with pytest.raises(TimeValidationError):
            provenance.validate()

    def test_empty_source_rejected(self):
        provenance = Provenance(source="", source_id="x", ingestion_time=at(12), event_time=at(11))
        with pytest.raises(ProvenanceError):
            provenance.validate()

    def test_naive_timestamp_rejected(self):
        from datetime import datetime

        provenance = Provenance(
            source="s", source_id="x",
            ingestion_time=datetime(2026, 9, 23, 12, 0), event_time=at(11),
        )
        with pytest.raises(TimeValidationError):
            provenance.validate()


class TestCausalChain:
    def test_valid_chain_passes(self):
        nodes = validate_causal_chain(_chain())
        assert len(nodes) == 4

    def test_events_join_chain(self):
        correlation = new_identifier("correlation_id")
        root = make_event(correlation_id=correlation)
        child = make_event(
            correlation_id=correlation, causation_id=root.event_id
        )
        validate_causal_chain([root, child])

    def test_missing_correlation_rejected(self):
        nodes = _chain()
        nodes[1].correlation_id = ""
        with pytest.raises(CausalityError):
            validate_causal_chain(nodes)

    def test_none_correlation_rejected(self):
        nodes = _chain()
        nodes[1].correlation_id = None
        with pytest.raises(CausalityError):
            validate_causal_chain(nodes)

    def test_unknown_causation_rejected(self):
        nodes = _chain()
        nodes[2].causation_id = "evt_" + "f" * 32
        with pytest.raises(CausalityError):
            validate_causal_chain(nodes)

    def test_causation_cycle_rejected(self):
        correlation = new_identifier("correlation_id")
        a = ChainNode("evt_" + "a" * 32, correlation, None)
        b = ChainNode("evt_" + "b" * 32, correlation, a.event_id)
        c = ChainNode("evt_" + "c" * 32, correlation, b.event_id)
        a.causation_id = c.event_id
        with pytest.raises(CausalityError):
            validate_causal_chain([a, b, c])

    def test_no_root_rejected(self):
        correlation = new_identifier("correlation_id")
        a = ChainNode("evt_" + "a" * 32, correlation, None)
        b = ChainNode("evt_" + "b" * 32, correlation, a.event_id)
        a.causation_id = b.event_id
        with pytest.raises(CausalityError):
            validate_causal_chain([a, b])

    def test_multiple_roots_rejected(self):
        correlation = new_identifier("correlation_id")
        a = ChainNode("evt_" + "a" * 32, correlation, None)
        b = ChainNode("evt_" + "b" * 32, correlation, None)
        with pytest.raises(CausalityError):
            validate_causal_chain([a, b])

    def test_mixed_correlations_rejected(self):
        nodes = _chain()
        nodes[2].correlation_id = new_identifier("correlation_id")
        with pytest.raises(CausalityError):
            validate_causal_chain(nodes)

    def test_duplicate_node_ids_rejected(self):
        nodes = _chain()
        nodes[1].event_id = nodes[0].event_id
        with pytest.raises(CausalityError):
            validate_causal_chain(nodes)
