"""Phase 7 benchmark (SECTION 110). LOCAL DEVELOPMENT BENCHMARK ONLY.

SYNTHETIC / LOCAL / NON-PRODUCTION: deterministic synthetic observations,
standard-library model families, single-process CPU execution. These
numbers say nothing about production throughput.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.phase7_factories import (  # noqa: E402
    KEY_MOM,
    KEY_VOL,
    feature_engine,
    research_dataset,
    trained_classifier,
)
from core.intelligence.adapter import ADAPTERS  # noqa: E402
from core.intelligence.dataset import (  # noqa: E402
    IntelligenceDatasetBuilder,
)
from core.intelligence.drift import DriftEngine  # noqa: E402
from core.intelligence.evaluation import ModelEvaluator  # noqa: E402
from core.intelligence.explain import ExplainabilityEngine  # noqa: E402
from core.intelligence.inference import (  # noqa: E402
    AISafetyValidator,
    InferenceEngine,
)
from core.intelligence.replay import IntelligenceReplayAdapter  # noqa: E402

ITERATIONS = 30


def stats(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)

    def pct(p: float) -> float:
        return ordered[min(int(p * len(ordered)), len(ordered) - 1)]

    return {
        "p50_ms": round(pct(0.50), 3),
        "p95_ms": round(pct(0.95), 3),
        "p99_ms": round(pct(0.99), 3),
        "max_ms": round(ordered[-1], 3),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "n": len(ordered),
    }


def bench(fn, *args, **kwargs):
    samples = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        fn(*args, **kwargs)
        samples.append((time.perf_counter() - start) * 1000.0)
    return stats(samples)


def main() -> None:
    dataset = research_dataset()
    engine = feature_engine()
    env = trained_classifier(dataset=dataset, engine=engine)
    definitions = (env["mom"], env["vol"])
    as_of = dataset.observations[50].available_time

    feature_stats = bench(
        lambda: engine.snapshot(dataset=dataset, definitions=definitions,
                                symbol="EURUSD", as_of=as_of,
                                environment="RESEARCH"))

    builder = IntelligenceDatasetBuilder(engine)
    from tests.phase7_factories import label_definition, pit_labels, \
        split_definition
    from core.intelligence.contracts import IntelligenceDatasetType
    dataset_stats = bench(
        lambda: builder.build(
            source=dataset, dataset_type=IntelligenceDatasetType.TRAIN,
            definitions=definitions, split=split_definition(),
            symbol="EURUSD", normalization_version="norm-1",
            label=label_definition(), label_values=pit_labels(dataset),
            start=dataset.observations[3].available_time,
            end=dataset.observations[40].available_time))

    model = env["outcome"].model

    def load_and_predict():
        inference = InferenceEngine(env["registry"], env["adapter"],
                                    AISafetyValidator())
        snapshot = engine.snapshot(
            dataset=dataset, definitions=definitions, symbol="EURUSD",
            as_of=as_of, environment="RESEARCH")
        inference.infer(model_id=model.model_id,
                        model_version=model.model_version,
                        inference_time=snapshot.available_at,
                        environment="RESEARCH", snapshot=snapshot)

    inference_stats = bench(load_and_predict)

    snapshot = engine.snapshot(dataset=dataset, definitions=definitions,
                               symbol="EURUSD", as_of=as_of,
                               environment="RESEARCH")
    evaluator = env["evaluator"]
    evaluation_stats = bench(
        lambda: evaluator.evaluate(
            model_hash=model.model_hash,
            dataset_hash=env["idataset"].content_hash,
            artifact_json=env["outcome"].artifact_json,
            rows=env["labelled"], environment="RESEARCH",
            created_at=dataset.observations[0].event_time))

    explainer = ExplainabilityEngine(env["adapter"])
    explanation_stats = bench(
        lambda: explainer.explain(
            model=model, feature_values={KEY_MOM: 0.4, KEY_VOL: 0.2},
            feature_hash=snapshot.feature_hash, inference_ref=None,
            environment="RESEARCH",
            generated_at=dataset.observations[0].event_time))

    drift_engine = DriftEngine()
    reference = [1.0] * 40
    current = [1.0] * 20 + [1.2] * 20
    drift_stats = bench(
        lambda: drift_engine.feature_drift(
            model_hash=model.model_hash, feature_key=KEY_MOM,
            reference=reference, current=current,
            created_at=dataset.observations[0].event_time))

    replay_adapter = IntelligenceReplayAdapter(engine, env["adapter"])
    moments = [dataset.observations[i].available_time
               for i in range(10, 20)]
    original = [env["adapter"].predict(
        env["outcome"].artifact_json,
        engine.snapshot(dataset=dataset, definitions=definitions,
                        symbol="EURUSD", as_of=m,
                        environment="REPLAY").values) for m in moments]
    replay_stats = bench(
        lambda: replay_adapter.replay(
            model=model, source=dataset, definitions=definitions,
            symbol="EURUSD", moments=moments, original_outputs=original,
            config_hash=env["config"].config_hash,
            created_at=dataset.observations[0].event_time))

    report = {
        "label": "SYNTHETIC / LOCAL / NON-PRODUCTION",
        "dataset": {"observations": len(dataset.observations),
                    "symbols": list(dataset.symbols),
                    "source": "SYNTHETIC deterministic fixture"},
        "model": {"family": "CLASSIFIER (stdlib deterministic logistic)",
                  "feature_count": 2, "training_rows":
                      len(env["labelled"])},
        "runtime": {"cpu": True, "gpu": False, "single_process": True},
        "feature_generation": feature_stats,
        "dataset_generation_38_rows": dataset_stats,
        "inference_cold_engine": inference_stats,
        "evaluation_30_rows": evaluation_stats,
        "explanation": explanation_stats,
        "drift_check": drift_stats,
        "replay_10_moments": replay_stats,
        "iterations_per_measurement": ITERATIONS,
        "notes": [
            "Deterministic stdlib model families; no external ML framework.",
            "Numbers measure this machine's Python interpreter only.",
            "Never interpret as production throughput.",
        ],
    }

    target = Path(__file__).resolve().parents[1] / "docs" / "phase-7"
    target.mkdir(parents=True, exist_ok=True)
    (target / "phase-7-benchmark.json").write_text(
        __import__("json").dumps(report, indent=2), encoding="utf-8")
    print(__import__("json").dumps(report, indent=2))


if __name__ == "__main__":
    main()
