"""Model evaluation (owned by core.intelligence).

Deterministic, versioned evaluation evidence: classification/regression
metrics, calibration, confidence distribution, uncertainty, stability and
coverage (SECTION 19/20/21). Metrics are never fabricated: insufficient
labels produce an explicit INSUFFICIENT_LABELS marker. No single magic
score decides production eligibility.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import ModelAdapter, ModelAdapterError
from core.intelligence.contracts import (
    ModelEvaluation,
    TaskType,
    UncertaintyStatus,
    canonical_hash,
)

CONTRACT_VERSION = "1.0.0"
METRICS_VERSION = "1.0.0"


class EvaluationError(ContractError):
    rule_id = "MEVAL"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def classification_metrics(labels: Sequence[str],
                           predicted: Sequence[str]) -> dict[str, Any]:
    """Precision/recall/F1/balanced accuracy + confusion matrix per class
    (one-vs-rest). ROC/PR-AUC need scores and are marked NOT_AVAILABLE for
    label-only evaluation - never fabricated."""
    if len(labels) != len(predicted):
        raise EvaluationError(
            "labels and predictions must align",
            location="eval.classification", rule_id="MEVAL-002")
    classes = sorted(set(labels) | set(predicted))
    confusion = {c: {d: 0 for d in classes} for c in classes}
    for actual, pred in zip(labels, predicted):
        confusion[actual][pred] += 1
    per_class: dict[str, dict[str, float]] = {}
    for cls in classes:
        tp = confusion[cls][cls]
        fp = sum(confusion[other][cls] for other in classes if other != cls)
        fn = sum(confusion[cls][other] for other in classes if other != cls)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else None)
        per_class[cls] = {"precision": precision, "recall": recall, "f1": f1,
                          "support": float(tp + fn)}
    total = len(labels)
    accuracy = sum(1 for a, p in zip(labels, predicted) if a == p) / total \
        if total else None
    return {
        "accuracy": accuracy,
        "balanced_accuracy": _mean([
            per_class[c]["recall"] for c in classes
            if per_class[c]["recall"] is not None]) if classes else None,
        "per_class": per_class,
        "confusion_matrix": confusion,
        "roc_auc": "NOT_AVAILABLE (requires continuous scores)",
        "pr_auc": "NOT_AVAILABLE (requires continuous scores)",
    }


def regression_metrics(labels: Sequence[float],
                       predicted: Sequence[float]) -> dict[str, Any]:
    if len(labels) != len(predicted):
        raise EvaluationError(
            "labels and predictions must align",
            location="eval.regression", rule_id="MEVAL-002")
    errors = [p - y for y, p in zip(labels, predicted)]
    mae = _mean([abs(e) for e in errors])
    rmse = math.sqrt(_mean([e * e for e in errors]))
    variance = _mean([(y - _mean(labels)) ** 2 for y in labels])
    r2 = 1 - (_mean([e * e for e in errors]) / variance) if variance > 0 \
        else None
    nonzero = [abs(y) for y in labels if abs(y) > 1e-12]
    mape = (_mean([abs(e) / abs(y) for e, y in zip(errors, labels)
                   if abs(y) > 1e-12])
            if len(nonzero) == len(labels) and nonzero else
            "NOT_AVAILABLE (zero/near-zero labels present)")
    return {"mae": mae, "rmse": rmse, "r2": r2, "mape": mape}


def calibration_curve(probabilities: Sequence[float],
                      labels: Sequence[float],
                      buckets: int = 5) -> dict[str, Any]:
    """Reliability buckets: predicted probability vs empirical frequency.
    Calibration evidence is stored, not asserted (SECTION 21)."""
    if len(probabilities) != len(labels):
        raise EvaluationError(
            "probabilities and labels must align",
            location="eval.calibration", rule_id="MEVAL-002")
    per_bucket = [{"lo": i / buckets, "hi": (i + 1) / buckets,
                   "n": 0, "sum_prob": 0.0, "sum_actual": 0.0}
                  for i in range(buckets)]
    for probability, actual in zip(probabilities, labels):
        index = min(int(probability * buckets), buckets - 1)
        bucket = per_bucket[index]
        bucket["n"] += 1
        bucket["sum_prob"] += probability
        bucket["sum_actual"] += float(actual)
    rows = []
    for bucket in per_bucket:
        n = bucket["n"]
        rows.append({
            "lo": bucket["lo"], "hi": bucket["hi"], "n": n,
            "mean_predicted": bucket["sum_prob"] / n if n else None,
            "empirical_frequency": bucket["sum_actual"] / n if n else None,
        })
    return {"buckets": rows, "bucket_count": buckets}


class ModelEvaluator:
    """Produces immutable ModelEvaluation evidence for one model+dataset."""

    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    def evaluate(self, *, model_hash: str, dataset_hash: str,
                 artifact_json: str,
                 rows: Sequence[Mapping[str, Any]],
                 environment: str,
                 created_at: datetime,
                 regime_split: Mapping[str, list[int]] | None = None,
                 symbol_split: Mapping[str, list[int]] | None = None,
                 horizon_metrics: Mapping[str, Any] | None = None) -> ModelEvaluation:
        task = self._adapter.task_type()
        labels: list[Any] = []
        predictions: list[Any] = []
        probabilities: list[float] = []
        missing = 0
        for row in rows:
            label = row.get("label")
            if label in (None, "UNKNOWN"):
                missing += 1
                continue
            try:
                output = self._adapter.predict(artifact_json,
                                               row["features"])
            except ModelAdapterError:
                missing += 1
                continue
            labels.append(label)
            if task is TaskType.CLASSIFICATION:
                predictions.append(output.get("label", "UNKNOWN"))
                if "probability" in output:
                    probabilities.append(float(output["probability"]))
            else:
                predictions.append(output.get("prediction"))
                if "probability" in output:
                    probabilities.append(float(output["probability"]))
        labelled = len(labels)
        if labelled == 0:
            metrics: dict[str, Any] = {
                "INSUFFICIENT_LABELS": True,
                "labelled_rows": 0,
                "note": "metrics are not fabricated without labels",
            }
        elif task is TaskType.CLASSIFICATION:
            metrics = classification_metrics(
                [str(x) for x in labels], [str(x) for x in predictions])
            metrics["INSUFFICIENT_LABELS"] = False
        else:
            metrics = regression_metrics(
                [float(x) for x in labels], [float(x) for x in predictions])
            metrics["INSUFFICIENT_LABELS"] = False
        calibration = None
        if probabilities and task is TaskType.CLASSIFICATION:
            calibration = calibration_curve(
                probabilities,
                [1.0 if str(x) == "UP" else 0.0 for x in labels])
        stability: dict[str, Any] = {
            "temporal": _stability_by_bucket(predictions, labelled, 4),
        }
        if regime_split:
            stability["regime"] = {
                name: _bucket_agreement(predictions, indices)
                for name, indices in regime_split.items()}
        if symbol_split:
            stability["symbol"] = {
                name: _bucket_agreement(predictions, indices)
                for name, indices in symbol_split.items()}
        if horizon_metrics is not None and task is TaskType.TIME_SERIES:
            stability["horizon"] = dict(horizon_metrics)
        confidence_distribution = {
            "n": labelled,
            "mean_probability": _mean(probabilities) if probabilities
            else "NOT_AVAILABLE",
        }
        uncertainty_summary = {
            "epistemic": "NOT_AVAILABLE",
            "aleatoric": "NOT_AVAILABLE",
            "missing_data": missing,
            "out_of_distribution": "NOT_AVAILABLE",
            "status": UncertaintyStatus.NOT_AVAILABLE.value,
        }
        coverage = {
            "labelled_rows": labelled,
            "unlabelled_rows": missing,
            "missingness": missing / max(len(rows), 1),
        }
        evaluation = ModelEvaluation(
            model_evaluation_id=new_identifier("model_evaluation_id"),
            model_hash=model_hash,
            dataset_hash=dataset_hash,
            task_type=task,
            metrics=metrics,
            metrics_version=METRICS_VERSION,
            calibration=calibration,
            confidence_distribution=confidence_distribution,
            uncertainty_summary=uncertainty_summary,
            stability=stability,
            coverage=coverage,
            created_at=ensure_utc(created_at, location="eval.created_at"),
            environment=environment,
            evidence_hash="",
        )
        object.__setattr__(evaluation, "evidence_hash",
                           evaluation.compute_evidence_hash())
        evaluation.validate()
        return evaluation


def _stability_by_bucket(predictions: Sequence[Any], n: int,
                         buckets: int) -> dict[str, Any]:
    if n == 0:
        return {"status": "UNKNOWN"}
    per_bucket = []
    size = max(n // buckets, 1)
    for i in range(0, n, size):
        chunk = predictions[i:i + size]
        counts: dict[str, int] = {}
        for value in chunk:
            key = str(value)
            counts[key] = counts.get(key, 0) + 1
        dominant = max(counts.values()) / len(chunk) if chunk else 0.0
        per_bucket.append({"chunk": len(per_bucket),
                           "dominant_share": dominant})
    spread = (max(b["dominant_share"] for b in per_bucket) -
              min(b["dominant_share"] for b in per_bucket)) \
        if per_bucket else 0.0
    status = "STABLE" if spread <= 0.25 else "UNSTABLE"
    return {"status": status, "spread": spread, "buckets": per_bucket}


def _bucket_agreement(predictions: Sequence[Any],
                      indices: Sequence[int]) -> dict[str, Any]:
    selected = [predictions[i] for i in indices if i < len(predictions)]
    if not selected:
        return {"status": "UNKNOWN"}
    counts: dict[str, int] = {}
    for value in selected:
        counts[str(value)] = counts.get(str(value), 0) + 1
    dominant = max(counts.values()) / len(selected)
    return {"status": "STABLE" if dominant >= 0.6 else "UNSTABLE",
            "dominant_share": dominant, "n": len(selected)}
