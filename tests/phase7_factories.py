"""Phase 7 test factories (SYNTHETIC data only).

Controlled fixtures, fully separated from production: deterministic
synthetic observations, features, labels, datasets and trained models.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research.contracts import Observation, ResearchDataset

from core.intelligence.contracts import (
    FeatureDefinition,
    FeatureStatus,
    IntelligenceDatasetType,
    LabelDefinition,
    MissingValuePolicy,
    TrainingConfig,
    UnknownPolicy,
)
from core.intelligence.feature import FeatureEngine, implementation_hash
from core.intelligence.dataset import (
    IntelligenceDatasetBuilder,
    SplitDefinition,
    label_available_at,
)
from core.intelligence.adapter import ADAPTERS
from core.intelligence.registry import ModelRegistry
from core.intelligence.training import TrainingService
from core.intelligence.evaluation import ModelEvaluator
from core.intelligence.inference import AISafetyValidator, InferenceEngine

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
BAR_SECONDS = 60
N_BARS = 60


def synthetic_observations(n: int = N_BARS, symbol: str = "EURUSD"):
    """SYNTHETIC price path: deterministic pseudo-random walk (no RNG state:
    closed-form sequence, reproducible everywhere)."""
    observations = []
    for i in range(n):
        price = 100 + (i % 7) + 0.3 * ((i * 37) % 11 - 5)
        event_time = T0 + timedelta(seconds=i * BAR_SECONDS)
        observations.append(Observation(
            symbol=symbol, event_time=event_time,
            available_time=event_time + timedelta(seconds=30),
            payload={"close": f"{price:.4f}", "volume": "100"}))
    return observations


def research_dataset(observations=None) -> ResearchDataset:
    observations = observations or synthetic_observations()
    dataset = ResearchDataset(
        dataset_id="dts_" + "a" * 32, dataset_version="1.0.0",
        symbols=("EURUSD",),
        time_range={"start": observations[0].event_time.isoformat(),
                    "end": observations[-1].event_time.isoformat()},
        timeframe="M1", timezone="UTC", data_source="SYNTHETIC",
        source_versions={"generator": "1.0.0"},
        quality_summary={"status": "SYNTHETIC", "checks": "controlled"},
        lineage={"origin": "synthetic fixture (never real market data)"},
        content_hash="", created_at=T0, environment="RESEARCH",
        observations=tuple(observations))
    object.__setattr__(dataset, "content_hash", dataset.compute_content_hash())
    dataset.validate()
    return dataset


def momentum(window):
    closes = [float(o.payload["close"]) for o in window]
    if len(closes) < 2:
        return None
    return closes[-1] - closes[-2]


def volatility(window):
    closes = [float(o.payload["close"]) for o in window]
    if len(closes) < 3:
        return None
    rets = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    mean = sum(rets) / len(rets)
    return (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5


def make_feature(fid: str, name: str, function, lookback: int) \
        -> FeatureDefinition:
    definition = FeatureDefinition(
        feature_id=fid, feature_version="1.0.0", name=name,
        description=f"SYNTHETIC smoke feature {name}",
        input_schema={"close": "decimal"},
        output_schema={"value": "float"},
        calculation_definition=f"deterministic: {name}",
        event_time_semantics="BAR_EVENT_TIME",
        available_time_semantics="AVAILABLE_AT",
        lookback_window=lookback, dependencies=("close",),
        normalization_definition={"fitted_on": "TRAIN",
                                  "method": "none"},
        missing_value_policy=MissingValuePolicy.UNKNOWN,
        unknown_policy=UnknownPolicy.PROPAGATE_UNKNOWN,
        timezone="UTC", source_provenance={"source": "SYNTHETIC"},
        content_hash="", implementation_hash=implementation_hash(function),
        status=FeatureStatus.ACTIVE)
    object.__setattr__(definition, "content_hash",
                       definition.compute_content_hash())
    definition.validate()
    return definition


FTR_MOM = "ftr_" + "1" * 32
FTR_VOL = "ftr_" + "2" * 32
KEY_MOM = f"{FTR_MOM}@1.0.0"
KEY_VOL = f"{FTR_VOL}@1.0.0"


def feature_engine() -> FeatureEngine:
    engine = FeatureEngine()
    engine.register(make_feature(FTR_MOM, "momentum_1", momentum, 2),
                    momentum)
    engine.register(make_feature(FTR_VOL, "volatility_2", volatility, 3),
                    volatility)
    return engine


def label_definition() -> LabelDefinition:
    label = LabelDefinition(
        label_definition_id="lbl_" + "3" * 32, label_version="1.0.0",
        target_definition="direction of next-2-bar return (SYNTHETIC)",
        horizon_bars=2,
        availability_rule="AVAILABLE_AT_EVENT_PLUS_HORIZON",
        calculation="UP if close[t+2] > close[t] else DOWN",
        leakage_rules=("label unknown before event_time + 2 bars",
                       "never a feature"),
        content_hash="")
    object.__setattr__(label, "content_hash", label.compute_content_hash())
    label.validate()
    return label


def split_definition() -> SplitDefinition:
    return SplitDefinition(
        split_id="fixture-split", semantics="TEMPORAL",
        overlap_declared=False,
        boundaries={
            "train_end": (T0 + timedelta(minutes=30)).isoformat(),
            "validation_end": (T0 + timedelta(minutes=40)).isoformat(),
            "oos_end": (T0 + timedelta(minutes=55)).isoformat()})


def pit_labels(dataset: ResearchDataset, horizon: int = 2):
    """Labels knowable at each as_of moment: at bar i the newest knowable
    label is for bar i-horizon (its horizon has fully passed)."""
    closes = [float(o.payload["close"]) for o in dataset.observations]
    labels = {}
    for i, o in enumerate(dataset.observations):
        j = i - horizon
        if j >= 0 and label_available_at(
                dataset.observations[j].event_time, horizon,
                BAR_SECONDS) <= o.available_time:
            labels[o.available_time.isoformat()] = \
                "UP" if closes[j + horizon] > closes[j] else "DOWN"
    return labels


def build_rows(engine: FeatureEngine, dataset: ResearchDataset,
               definitions, horizon: int = 2):
    labels = pit_labels(dataset, horizon)
    rows = []
    for i, o in enumerate(dataset.observations):
        if i < 3:
            continue
        snapshot = engine.snapshot(
            dataset=dataset, definitions=definitions, symbol="EURUSD",
            as_of=o.available_time, environment="RESEARCH")
        row = {"as_of": snapshot.as_of.isoformat(),
               "features": dict(snapshot.values)}
        row["label"] = labels.get(row["as_of"], "UNKNOWN")
        rows.append(row)
    return rows


def training_config(seed: int = 42) -> TrainingConfig:
    config = TrainingConfig(
        config_id="cfg_" + "4" * 32, config_version="1.0.0",
        model_family="CLASSIFIER",
        hyperparameters={"feature_keys": [KEY_MOM, KEY_VOL],
                         "learning_rate": 0.1, "epochs": 50,
                         "threshold": "0.5", "l2": 0.0},
        seed=seed, feature_keys=(KEY_MOM, KEY_VOL),
        thresholds={"decision": "0.5"},
        resource_limits={"max_training_seconds": 60,
                         "max_memory_mb": 256},
        config_hash="", environment="RESEARCH")
    object.__setattr__(config, "config_hash",
                       config.compute_config_hash())
    config.validate()
    return config


def trained_classifier(dataset=None, engine=None, seed: int = 42):
    """Full deterministic chain: dataset -> features -> labels -> model."""
    dataset = dataset or research_dataset()
    engine = engine or feature_engine()
    definitions = (engine.get(FTR_MOM, "1.0.0")
                   if hasattr(engine, "get") else None)
    mom = make_feature(FTR_MOM, "momentum_1", momentum, 2)
    vol = make_feature(FTR_VOL, "volatility_2", volatility, 3)
    builder = IntelligenceDatasetBuilder(engine)
    labels = pit_labels(dataset)
    result = builder.build(
        source=dataset, dataset_type=IntelligenceDatasetType.TRAIN,
        definitions=(mom, vol), split=split_definition(), symbol="EURUSD",
        normalization_version="norm-1", label=label_definition(),
        label_values=labels, start=dataset.observations[3].available_time,
        end=T0 + timedelta(minutes=30))
    rows = build_rows(engine, dataset, (mom, vol))
    labelled = [r for r in rows if r["label"] != "UNKNOWN"][:30]
    registry = ModelRegistry()
    trainer = TrainingService(registry, ADAPTERS["CLASSIFIER"])
    config = training_config(seed)
    outcome = trainer.train(
        config=config, dataset=result.dataset, rows=labelled,
        model_id="mdl_" + "5" * 32, model_version="1.0.0",
        started_at=T0, trainer_source="phase7 fixture")
    return {"dataset": dataset, "engine": engine, "mom": mom, "vol": vol,
            "idataset": result.dataset, "rows": rows, "labelled": labelled,
            "registry": registry, "config": config, "outcome": outcome,
            "adapter": ADAPTERS["CLASSIFIER"],
            "evaluator": ModelEvaluator(ADAPTERS["CLASSIFIER"]),
            "safety": AISafetyValidator(),
            "inferencer": InferenceEngine(registry, ADAPTERS["CLASSIFIER"],
                                          AISafetyValidator())}
