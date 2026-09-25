"""Model adapter boundary (owned by core.intelligence).

Core depends on the ModelAdapter contract, never on a vendor/framework
(SECTION 14/15). The built-in families are deterministic statistical
estimators implemented on the standard library: same inputs + same seed ->
bit-identical artifacts. Their nature is recorded honestly in every model
card; nothing here fabricates intelligence.

Unsupported operations return explicit UNSUPPORTED - never silent fallback.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError

from core.intelligence.contracts import TaskType

CONTRACT_VERSION = "1.0.0"
FRAMEWORK_VERSION = "stdlib-deterministic-1.0"


class AdapterCapability(Enum):
    CLASSIFICATION = "CLASSIFICATION"
    REGRESSION = "REGRESSION"
    ANOMALY_DETECTION = "ANOMALY_DETECTION"
    PROBABILITY = "PROBABILITY"
    CONFIDENCE = "CONFIDENCE"
    UNCERTAINTY = "UNCERTAINTY"
    FEATURE_CONTRIBUTION = "FEATURE_CONTRIBUTION"
    OOD_DISTANCE = "OOD_DISTANCE"


class ModelAdapterError(ContractError):
    rule_id = "ADAPTER"


@dataclass(frozen=True)
class AdapterMetadata:
    model_family: str
    capabilities: tuple[AdapterCapability, ...]
    input_schema: Mapping[str, str]
    output_schema: Mapping[str, str]
    required_hyperparameters: tuple[str, ...]


class ModelAdapter:
    """Port: the intelligence core talks to models only through this."""

    def metadata(self) -> AdapterMetadata:
        raise ModelAdapterError(
            "metadata not implemented for this adapter",
            location="adapter.metadata", rule_id="ADAPTER-001")

    def supports(self, capability: AdapterCapability) -> bool:
        return capability in self.metadata().capabilities

    def train(self, rows: Sequence[Mapping[str, Any]],
              hyperparameters: Mapping[str, Any], seed: int) -> str:
        raise ModelAdapterError(
            f"train is UNSUPPORTED for {type(self).__name__}",
            location="adapter.train", rule_id="ADAPTER-UNSUPPORTED")

    def predict(self, artifact_json: str,
                features: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ModelAdapterError(
            f"predict is UNSUPPORTED for {type(self).__name__}",
            location="adapter.predict", rule_id="ADAPTER-UNSUPPORTED")

    def explain(self, artifact_json: str,
                features: Mapping[str, Any]) -> Mapping[str, Any]:
        raise ModelAdapterError(
            f"explain is UNSUPPORTED for {type(self).__name__}",
            location="adapter.explain", rule_id="ADAPTER-UNSUPPORTED")

    def task_type(self) -> TaskType:
        raise ModelAdapterError(
            "task_type is UNSUPPORTED", location="adapter.task_type",
            rule_id="ADAPTER-UNSUPPORTED")


def _artifact_hash(payload: Mapping[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _numeric(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            return float(str(value))
        except (TypeError, ValueError) as error:
            raise ModelAdapterError(
                f"feature value {value!r} is not numeric",
                location="adapter.features", rule_id="ADAPTER-002",
                details={"value": str(value)}) from error
    return float(value)


def _parse_artifact(artifact_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(artifact_json)
    except json.JSONDecodeError as error:
        raise ModelAdapterError(
            "malformed model artifact (unsafe input rejected)",
            location="adapter.artifact", rule_id="ADAPTER-003") from error
    if not isinstance(payload, dict) or "artifact_hash" not in payload:
        raise ModelAdapterError(
            "artifact must be a mapping carrying its artifact_hash",
            location="adapter.artifact", rule_id="ADAPTER-003")
    if payload["artifact_hash"] != _artifact_hash(
            {k: v for k, v in payload.items() if k != "artifact_hash"}):
        raise ModelAdapterError(
            "artifact hash mismatch (corrupted or forged artifact rejected)",
            location="adapter.artifact", rule_id="ADAPTER-004")
    return payload


def _feature_vector(features: Mapping[str, Any],
                    keys: Sequence[str]) -> list[float]:
    vector = []
    for key in keys:
        if key not in features or features[key] in (None, "UNKNOWN"):
            raise ModelAdapterError(
                f"feature '{key}' is missing/UNKNOWN - inference cannot "
                "proceed on incomplete inputs",
                location="adapter.features", rule_id="ADAPTER-005",
                details={"feature": key})
        vector.append(_numeric(features[key]))
    return vector


# --------------------------------------------------------------------- #
# BASELINE: deterministic reference estimators                           #
# --------------------------------------------------------------------- #
class BaselineRegressor(ModelAdapter):
    """Predicts the training-set mean. Reference model: honest, weak,
    deterministic."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="BASELINE",
            capabilities=(AdapterCapability.REGRESSION,
                          AdapterCapability.CONFIDENCE),
            input_schema={"features": "numeric vector"},
            output_schema={"prediction": "float"},
            required_hyperparameters=(),
        )

    def task_type(self) -> TaskType:
        return TaskType.REGRESSION

    def train(self, rows, hyperparameters, seed):
        targets = [float(r["label"]) for r in rows if r.get("label") not in
                   (None, "UNKNOWN")]
        if not targets:
            raise ModelAdapterError(
                "baseline regression requires numeric labels "
                "(INSUFFICIENT_LABELS - never fabricate)",
                location="baseline.train", rule_id="ADAPTER-006")
        payload = {"family": "BASELINE", "mean": sum(targets) / len(targets),
                   "n": len(targets), "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        n = max(artifact["n"], 1)
        return {"prediction": artifact["mean"],
                "confidence": min(1.0, n / 100.0)}


class BaselineClassifier(ModelAdapter):
    """Predicts the training-set majority class with its empirical rate as
    probability."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="BASELINE",
            capabilities=(AdapterCapability.CLASSIFICATION,
                          AdapterCapability.PROBABILITY),
            input_schema={"features": "numeric vector"},
            output_schema={"label": "string"},
            required_hyperparameters=(),
        )

    def task_type(self) -> TaskType:
        return TaskType.CLASSIFICATION

    def train(self, rows, hyperparameters, seed):
        labels = [str(r["label"]) for r in rows if r.get("label") not in
                  (None, "UNKNOWN")]
        if not labels:
            raise ModelAdapterError(
                "baseline classification requires labels "
                "(INSUFFICIENT_LABELS)",
                location="baseline.train", rule_id="ADAPTER-006")
        counts: dict[str, int] = {}
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
        majority = max(sorted(counts), key=lambda k: counts[k])
        payload = {"family": "BASELINE", "majority": majority,
                   "probability": counts[majority] / len(labels),
                   "n": len(labels), "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        return {"label": artifact["majority"],
                "probability": artifact["probability"]}


# --------------------------------------------------------------------- #
# LINEAR: closed-form least squares (fully deterministic)                #
# --------------------------------------------------------------------- #
class LinearRegressor(ModelAdapter):
    """Ridge-regularized least squares via Gaussian elimination on the
    normal equations. Closed form: no iteration order, no randomness."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="LINEAR",
            capabilities=(AdapterCapability.REGRESSION,
                          AdapterCapability.CONFIDENCE,
                          AdapterCapability.FEATURE_CONTRIBUTION),
            input_schema={"features": "numeric vector"},
            output_schema={"prediction": "float"},
            required_hyperparameters=("feature_keys", "l2"),
        )

    def task_type(self) -> TaskType:
        return TaskType.REGRESSION

    def train(self, rows, hyperparameters, seed):
        for key in self.metadata().required_hyperparameters:
            if key not in hyperparameters:
                raise ModelAdapterError(
                    f"hyperparameter '{key}' missing (NO_IMPLICIT_DEFAULTS)",
                    location="linear.train", rule_id="ADAPTER-007",
                    details={"missing": key})
        keys = list(hyperparameters["feature_keys"])
        l2 = float(hyperparameters["l2"])
        design, targets = [], []
        for row in rows:
            label = row.get("label")
            if label in (None, "UNKNOWN"):
                continue
            design.append([1.0] + _feature_vector(row["features"], keys))
            targets.append(float(label))
        if len(design) < len(keys) + 1:
            raise ModelAdapterError(
                "insufficient labelled rows for linear fit",
                location="linear.train", rule_id="ADAPTER-006")
        dim = len(keys) + 1
        normal = [[0.0] * (dim + 1) for _ in range(dim)]
        for row_vec, target in zip(design, targets):
            for i in range(dim):
                for j in range(dim):
                    normal[i][j] += row_vec[i] * row_vec[j]
                normal[i][dim] += row_vec[i] * target
        for i in range(1, dim):
            normal[i][i] += l2
        normal[0][0] += 1e-12
        weights = _solve(normal)
        payload = {"family": "LINEAR", "feature_keys": keys,
                   "weights": weights, "l2": l2, "n": len(design),
                   "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        vector = _feature_vector(features, artifact["feature_keys"])
        prediction = artifact["weights"][0] + sum(
            w * v for w, v in zip(artifact["weights"][1:], vector))
        return {"prediction": prediction,
                "confidence": min(1.0, artifact["n"] / 100.0)}

    def explain(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        vector = _feature_vector(features, artifact["feature_keys"])
        mean = sum(artifact["weights"][1:]) / max(
            len(artifact["weights"]) - 1, 1)
        contributions = {
            key: w * v for key, w, v in zip(artifact["feature_keys"],
                                            artifact["weights"][1:], vector)}
        return {"intercept": artifact["weights"][0],
                "mean_weight": mean, "contributions": contributions}


def _solve(augmented: list[list[float]]) -> list[float]:
    """Gaussian elimination with partial pivoting (deterministic)."""
    matrix = [row[:] for row in augmented]
    n = len(matrix)
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            raise ModelAdapterError(
                "singular normal equations (degenerate training data)",
                location="linear.solve", rule_id="ADAPTER-008")
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        for row in range(col + 1, n):
            factor = matrix[row][col] / matrix[col][col]
            for k in range(col, n + 1):
                matrix[row][k] -= factor * matrix[col][k]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        acc = matrix[row][n] - sum(
            matrix[row][k] * solution[k] for k in range(row + 1, n))
        solution[row] = acc / matrix[row][row]
    return solution


# --------------------------------------------------------------------- #
# CLASSIFIER: thresholded logistic regression (full-batch, no shuffling) #
# --------------------------------------------------------------------- #
class ThresholdClassifier(ModelAdapter):
    """Deterministic full-batch gradient descent on the logistic loss.
    Fixed iteration count from hyperparameters; no randomness anywhere."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="CLASSIFIER",
            capabilities=(AdapterCapability.CLASSIFICATION,
                          AdapterCapability.PROBABILITY,
                          AdapterCapability.CONFIDENCE,
                          AdapterCapability.FEATURE_CONTRIBUTION),
            input_schema={"features": "numeric vector"},
            output_schema={"label": "UP|DOWN", "probability": "float"},
            required_hyperparameters=("feature_keys", "learning_rate",
                                      "epochs", "threshold", "l2"),
        )

    def task_type(self) -> TaskType:
        return TaskType.CLASSIFICATION

    def train(self, rows, hyperparameters, seed):
        for key in self.metadata().required_hyperparameters:
            if key not in hyperparameters:
                raise ModelAdapterError(
                    f"hyperparameter '{key}' missing (NO_IMPLICIT_DEFAULTS)",
                    location="classifier.train", rule_id="ADAPTER-007",
                    details={"missing": key})
        keys = list(hyperparameters["feature_keys"])
        rate = float(hyperparameters["learning_rate"])
        epochs = int(hyperparameters["epochs"])
        threshold = float(hyperparameters["threshold"])
        l2 = float(hyperparameters["l2"])
        data = []
        for row in rows:
            label = row.get("label")
            if label in (None, "UNKNOWN"):
                continue
            y = 1.0 if str(label) == "UP" else 0.0
            data.append((_feature_vector(row["features"], keys), y))
        if not data:
            raise ModelAdapterError(
                "classifier training requires labels (INSUFFICIENT_LABELS)",
                location="classifier.train", rule_id="ADAPTER-006")
        dim = len(keys)
        weights = [0.0] * dim
        bias = 0.0
        for _ in range(epochs):
            grad_w = [0.0] * dim
            grad_b = 0.0
            for vector, y in data:
                p = _sigmoid(bias + sum(w * v for w, v in zip(weights, vector)))
                err = p - y
                for i in range(dim):
                    grad_w[i] += err * vector[i]
                grad_b += err
            m = len(data)
            for i in range(dim):
                weights[i] -= rate * (grad_w[i] / m + l2 * weights[i])
            bias -= rate * grad_b / m
        payload = {"family": "CLASSIFIER", "feature_keys": keys,
                   "weights": weights, "bias": bias, "threshold": threshold,
                   "epochs": epochs, "learning_rate": rate, "l2": l2,
                   "n": len(data), "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        vector = _feature_vector(features, artifact["feature_keys"])
        probability = _sigmoid(artifact["bias"] + sum(
            w * v for w, v in zip(artifact["weights"], vector)))
        return {"label": "UP" if probability >= artifact["threshold"] else "DOWN",
                "probability": probability}

    def explain(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        vector = _feature_vector(features, artifact["feature_keys"])
        contributions = {
            key: w * v for key, w, v in zip(artifact["feature_keys"],
                                            artifact["weights"], vector)}
        return {"bias": artifact["bias"], "contributions": contributions}


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# --------------------------------------------------------------------- #
# ANOMALY: versioned z-score detector                                    #
# --------------------------------------------------------------------- #
class ZScoreAnomaly(ModelAdapter):
    """Flags feature vectors far from the training distribution.

    An anomaly is EVIDENCE for policy/risk - it never automatically stops
    trading or closes positions (SECTION 35)."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="ANOMALY",
            capabilities=(AdapterCapability.ANOMALY_DETECTION,
                          AdapterCapability.CONFIDENCE,
                          AdapterCapability.OOD_DISTANCE),
            input_schema={"features": "numeric vector"},
            output_schema={"anomaly_score": "float", "anomaly": "boolean"},
            required_hyperparameters=("feature_keys", "z_warning",
                                      "z_critical"),
        )

    def task_type(self) -> TaskType:
        return TaskType.ANOMALY

    def train(self, rows, hyperparameters, seed):
        for key in self.metadata().required_hyperparameters:
            if key not in hyperparameters:
                raise ModelAdapterError(
                    f"hyperparameter '{key}' missing (NO_IMPLICIT_DEFAULTS)",
                    location="anomaly.train", rule_id="ADAPTER-007",
                    details={"missing": key})
        keys = list(hyperparameters["feature_keys"])
        vectors = [_feature_vector(r["features"], keys) for r in rows]
        if len(vectors) < 2:
            raise ModelAdapterError(
                "anomaly model needs >= 2 rows",
                location="anomaly.train", rule_id="ADAPTER-006")
        means = [sum(col) / len(col) for col in zip(*vectors)]
        variances = [
            sum((row[i] - means[i]) ** 2 for row in vectors) / len(vectors)
            for i in range(len(keys))]
        payload = {"family": "ANOMALY", "feature_keys": keys,
                   "means": means, "stds": [math.sqrt(v) for v in variances],
                   "z_warning": float(hyperparameters["z_warning"]),
                   "z_critical": float(hyperparameters["z_critical"]),
                   "n": len(vectors), "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        vector = _feature_vector(features, artifact["feature_keys"])
        zmax = 0.0
        for value, mean, std in zip(vector, artifact["means"],
                                    artifact["stds"]):
            if std > 0:
                zmax = max(zmax, abs(value - mean) / std)
        return {"anomaly_score": zmax,
                "anomaly": zmax >= artifact["z_warning"],
                "severity": "CRITICAL" if zmax >= artifact["z_critical"]
                else ("WARNING" if zmax >= artifact["z_warning"] else "NORMAL")}


# --------------------------------------------------------------------- #
# REGIME: rule-based, versioned, UNKNOWN-honest                          #
# --------------------------------------------------------------------- #
class RegimeDetector(ModelAdapter):
    """Classifies volatility regime from declared thresholds. UNKNOWN is
    legitimate: insufficient evidence is never forced into a class
    (SECTION 34)."""

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            model_family="REGIME",
            capabilities=(AdapterCapability.CLASSIFICATION,
                          AdapterCapability.CONFIDENCE),
            input_schema={"volatility": "float"},
            output_schema={"regime": "string"},
            required_hyperparameters=("vol_thresholds",),
        )

    def task_type(self) -> TaskType:
        return TaskType.CLASSIFICATION

    def train(self, rows, hyperparameters, seed):
        if "vol_thresholds" not in hyperparameters:
            raise ModelAdapterError(
                "vol_thresholds missing (NO_IMPLICIT_DEFAULTS)",
                location="regime.train", rule_id="ADAPTER-007")
        payload = {"family": "REGIME",
                   "vol_thresholds": list(hyperparameters["vol_thresholds"]),
                   "n": len(rows), "seed": seed}
        payload["artifact_hash"] = _artifact_hash(payload)
        return json.dumps(payload, sort_keys=True)

    def predict(self, artifact_json, features):
        artifact = _parse_artifact(artifact_json)
        if "volatility" not in features or \
                features["volatility"] in (None, "UNKNOWN"):
            return {"regime": "UNKNOWN",
                    "reason": "volatility input missing/UNKNOWN"}
        volatility = _numeric(features["volatility"])
        low, high = artifact["vol_thresholds"]
        if volatility < low:
            regime = "LOW_VOLATILITY"
        elif volatility > high:
            regime = "HIGH_VOLATILITY"
        else:
            regime = "RANGE"
        return {"regime": regime}


ADAPTERS: dict[str, ModelAdapter] = {
    "BASELINE_REGRESSOR": BaselineRegressor(),
    "BASELINE_CLASSIFIER": BaselineClassifier(),
    "LINEAR": LinearRegressor(),
    "CLASSIFIER": ThresholdClassifier(),
    "ANOMALY": ZScoreAnomaly(),
    "REGIME": RegimeDetector(),
}
