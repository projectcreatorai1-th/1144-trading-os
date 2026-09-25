"""Synthetic market feed + intelligence stack for the desktop gateway.

SYNTHETIC data only, flowing through the REAL contracts: deterministic
price paths become Phase 6 point-in-time research observations; the
intelligence stack is the real Phase 7 engine chain (features ->
dataset -> deterministic classifier -> inference -> advisory proposal).
Nothing here fabricates trading results.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import ADAPTERS
from core.intelligence.contracts import (
    FeatureDefinition,
    FeatureStatus,
    IntelligenceDatasetType,
    LabelDefinition,
    MissingValuePolicy,
    UnknownPolicy,
)
from core.intelligence.dataset import IntelligenceDatasetBuilder
from core.intelligence.feature import FeatureEngine, implementation_hash
from core.intelligence.inference import AISafetyValidator, InferenceEngine
from core.intelligence.registry import ModelRegistry
from core.intelligence.training import TrainingService
from core.intelligence.outputs import ProposedDirection
from core.research.contracts import Observation, ResearchDataset

SYMBOLS = ("EURUSD", "XAUUSD", "US30")
FEED_LABEL = "SYNTHETIC"


def _path(symbol: str, index: int) -> float:
    """Deterministic pseudo-price per symbol/bar (closed form, no RNG)."""
    seed = sum(ord(c) for c in symbol)
    wave = (index * (7 + seed % 5)) % 13 - 6
    drift = 0.2 * ((index * 31 + seed) % 9 - 4)
    base = {"EURUSD": 1.0850, "XAUUSD": 2650.0, "US30": 39100.0}[symbol]
    return round(base + wave * (0.0015 if symbol == "EURUSD" else 1.0)
                 + drift * (0.0004 if symbol == "EURUSD" else 0.4), 5)


@dataclass(frozen=True)
class Feed:
    symbol: str
    observations: tuple[Observation, ...]

    @property
    def latest_price(self) -> str:
        return str(self.observations[-1].payload["close"])

    @property
    def latest_event_time(self) -> datetime:
        return self.observations[-1].event_time


def build_feed(symbol: str, bars: int, start: datetime) -> Feed:
    observations = []
    for index in range(bars):
        event_time = ensure_utc(start) + timedelta(minutes=index)
        observations.append(Observation(
            symbol=symbol, event_time=event_time,
            available_time=event_time + timedelta(seconds=30),
            payload={"close": str(_path(symbol, index)),
                     "volume": "100", "source": FEED_LABEL}))
    return Feed(symbol=symbol, observations=tuple(observations))


def build_dataset(feed: Feed, start: datetime) -> ResearchDataset:
    dataset = ResearchDataset(
        dataset_id=new_identifier("dataset_id"), dataset_version="1.0.0",
        symbols=(feed.symbol,),
        time_range={"start": feed.observations[0].event_time.isoformat(),
                    "end": feed.observations[-1].event_time.isoformat()},
        timeframe="M1", timezone="UTC", data_source=FEED_LABEL,
        source_versions={"generator": "deterministic-1.0"},
        quality_summary={"status": FEED_LABEL, "checks": "controlled"},
        lineage={"origin": "synthetic desktop feed"},
        content_hash="", created_at=ensure_utc(start), environment="RESEARCH",
        observations=feed.observations)
    object.__setattr__(dataset, "content_hash", dataset.compute_content_hash())
    dataset.validate()
    return dataset


# ------------------------------------------------------------------ #
# Feature implementations (deterministic, stdlib)                      #
# ------------------------------------------------------------------ #
def momentum(window):
    closes = [float(o.payload["close"]) for o in window]
    return closes[-1] - closes[-2] if len(closes) >= 2 else None


def volatility(window):
    closes = [float(o.payload["close"]) for o in window]
    if len(closes) < 3:
        return None
    rets = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5


def _feature(fid_tail: str, name: str, function, lookback: int,
             dataset_id: str) -> FeatureDefinition:
    definition = FeatureDefinition(
        feature_id="ftr_" + fid_tail * 32, feature_version="1.0.0",
        name=name, description=f"SYNTHETIC desktop feature {name}",
        input_schema={"close": "decimal"},
        output_schema={"value": "float"},
        calculation_definition=f"deterministic: {name}",
        event_time_semantics="BAR_EVENT_TIME",
        available_time_semantics="AVAILABLE_AT",
        lookback_window=lookback, dependencies=("close",),
        normalization_definition={"fitted_on": "TRAIN"},
        missing_value_policy=MissingValuePolicy.UNKNOWN,
        unknown_policy=UnknownPolicy.PROPAGATE_UNKNOWN,
        timezone="UTC", source_provenance={"dataset": dataset_id},
        content_hash="", implementation_hash=implementation_hash(function),
        status=FeatureStatus.ACTIVE)
    object.__setattr__(definition, "content_hash",
                       definition.compute_content_hash())
    definition.validate()
    return definition


@dataclass
class IntelligenceStack:
    engine: FeatureEngine
    registry: ModelRegistry
    model: Any
    artifact_json: str
    mom: FeatureDefinition
    vol: FeatureDefinition
    label: LabelDefinition


def build_intelligence(dataset: ResearchDataset,
                       start: datetime) -> IntelligenceStack:
    mom = _feature("1", "momentum_1", momentum, 2, dataset.dataset_id)
    vol = _feature("2", "volatility_2", volatility, 3, dataset.dataset_id)
    engine = FeatureEngine()
    engine.register(mom, momentum)
    engine.register(vol, volatility)

    label = LabelDefinition(
        label_definition_id=new_identifier("label_definition_id"),
        label_version="1.0.0",
        target_definition="direction of next-2-bar return (SYNTHETIC)",
        horizon_bars=2,
        availability_rule="AVAILABLE_AT_EVENT_PLUS_HORIZON",
        calculation="UP if close[t+2] > close[t] else DOWN",
        leakage_rules=("label unknown before event_time + 2 bars",),
        content_hash="")
    object.__setattr__(label, "content_hash", label.compute_content_hash())
    label.validate()

    definitions = (mom, vol)
    closes = [float(o.payload["close"]) for o in dataset.observations]
    rows = []
    for index, observation in enumerate(dataset.observations):
        if index < 3:
            continue
        snapshot = engine.snapshot(
            dataset=dataset, definitions=definitions,
            symbol=dataset.symbols[0], as_of=observation.available_time,
            environment="RESEARCH")
        j = index - 2
        labelled = "UNKNOWN"
        if j >= 0 and dataset.observations[j].available_time + \
                timedelta(seconds=120) <= observation.available_time:
            labelled = "UP" if closes[j + 2] > closes[j] else "DOWN"
        rows.append({"as_of": snapshot.as_of.isoformat(),
                     "features": dict(snapshot.values), "label": labelled})
    labelled_rows = [r for r in rows if r["label"] != "UNKNOWN"][:30]

    from core.intelligence.contracts import TrainingConfig
    keys = (f"{mom.feature_id}@1.0.0", f"{vol.feature_id}@1.0.0")
    config = TrainingConfig(
        config_id=new_identifier("config_id"), config_version="1.0.0",
        model_family="CLASSIFIER",
        hyperparameters={"feature_keys": list(keys),
                         "learning_rate": 0.1, "epochs": 50,
                         "threshold": "0.5", "l2": 0.0},
        seed=42, feature_keys=keys, thresholds={"decision": "0.5"},
        resource_limits={"max_training_seconds": 60,
                         "max_memory_mb": 256},
        config_hash="", environment="RESEARCH")
    object.__setattr__(config, "config_hash", config.compute_config_hash())
    config.validate()

    builder = IntelligenceDatasetBuilder(engine)
    from core.intelligence.dataset import SplitDefinition, label_available_at
    split = SplitDefinition(
        split_id="desktop-split", semantics="TEMPORAL",
        overlap_declared=False,
        boundaries={
            "train_end": (ensure_utc(start) + timedelta(minutes=30)).isoformat(),
            "validation_end": (ensure_utc(start) + timedelta(minutes=40)).isoformat(),
            "oos_end": (ensure_utc(start) + timedelta(minutes=55)).isoformat()})
    label_values = {r["as_of"]: r["label"] for r in rows}
    idataset = builder.build(
        source=dataset, dataset_type=IntelligenceDatasetType.TRAIN,
        definitions=definitions, split=split, symbol=dataset.symbols[0],
        normalization_version="norm-1", label=label,
        label_values=label_values,
        start=dataset.observations[3].available_time,
        end=ensure_utc(start) + timedelta(minutes=30)).dataset

    registry = ModelRegistry()
    trainer = TrainingService(registry, ADAPTERS["CLASSIFIER"])
    outcome = trainer.train(
        config=config, dataset=idataset, rows=labelled_rows,
        model_id=new_identifier("model_id"), model_version="1.0.0",
        started_at=ensure_utc(start), trainer_source="desktop gateway")
    if outcome.model is None:
        raise ContractError(
            "intelligence training failed (desktop stack cannot start "
            "on a failed model)",
            location="gateway.intelligence", rule_id="GUI-STACK")
    return IntelligenceStack(engine=engine, registry=registry,
                             model=outcome.model,
                             artifact_json=outcome.artifact_json,
                             mom=mom, vol=vol, label=label)


def build_inferencer(stack: IntelligenceStack) -> InferenceEngine:
    return InferenceEngine(stack.registry, ADAPTERS["CLASSIFIER"],
                           AISafetyValidator())


def snapshot_for(stack: IntelligenceStack, dataset: ResearchDataset,
                 symbol: str, as_of: datetime):
    return stack.engine.snapshot(
        dataset=dataset, definitions=(stack.mom, stack.vol),
        symbol=symbol, as_of=ensure_utc(as_of), environment="RESEARCH")


def advisory_direction(label_value: str) -> ProposedDirection:
    if label_value == "UP":
        return ProposedDirection.LONG
    if label_value == "DOWN":
        return ProposedDirection.SHORT
    return ProposedDirection.UNKNOWN
