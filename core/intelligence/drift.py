"""Drift engine + OOD detection (owned by core.intelligence).

Drift (SECTION 36/37): 7 drift types with versioned thresholds; status
NORMAL/WARNING/CRITICAL/UNKNOWN. Drift produces EVIDENCE and health state
only - it never modifies risk directly; existing Policy/Risk may consume
the evidence through explicit policy.

OOD (SECTION 39): distribution-distance evidence. OOD is never silently
transformed into an ordinary prediction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import canonical_hash
from core.intelligence.outputs import DriftReport, DriftStatus, DriftType, OODResult, OODStatus

CONTRACT_VERSION = "1.0.0"


class DriftError(ContractError):
    rule_id = "DRIFT"


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / len(values))


def _psi(expected: Sequence[float], observed: Sequence[float],
         bins: int = 5) -> float:
    """Population Stability Index over shared bins (deterministic)."""
    lo = min(expected + observed) if expected and observed else 0.0
    hi = max(expected + observed) if expected and observed else 1.0
    if hi <= lo:
        return 0.0
    width = (hi - lo) / bins

    def hist(values: Sequence[float]) -> list[float]:
        counts = [0] * bins
        for value in values:
            index = min(int((value - lo) / width), bins - 1)
            counts[index] += 1
        return [max(c / len(values), 1e-6) for c in counts] if values \
            else [1e-6] * bins

    e, o = hist(expected), hist(observed)
    return sum((o[i] - e[i]) * math.log(o[i] / e[i]) for i in range(bins))


#: Versioned thresholds (SECTION 36). Thresholds are configuration, never
#: magic numbers in comparison sites.
DEFAULT_DRIFT_THRESHOLDS = {"warning": 0.10, "critical": 0.25}
DEFAULT_OOD_THRESHOLDS = {"warning": 2.0, "critical": 3.5}
THRESHOLD_VERSION = "drift-thresholds-1.0.0"


@dataclass(frozen=True)
class DriftThresholds:
    warning: float
    critical: float
    version: str

    def classify(self, statistic: float | None) -> DriftStatus:
        if statistic is None or not isinstance(statistic, (int, float)):
            return DriftStatus.UNKNOWN
        if statistic >= self.critical:
            return DriftStatus.CRITICAL
        if statistic >= self.warning:
            return DriftStatus.WARNING
        return DriftStatus.NORMAL


class DriftEngine:
    """Computes drift reports from reference (training) vs current windows."""

    def __init__(self, thresholds: DriftThresholds | None = None,
                 ood_thresholds: DriftThresholds | None = None) -> None:
        self._thresholds = thresholds or DriftThresholds(
            warning=DEFAULT_DRIFT_THRESHOLDS["warning"],
            critical=DEFAULT_DRIFT_THRESHOLDS["critical"],
            version=THRESHOLD_VERSION)
        self._ood = ood_thresholds or DriftThresholds(
            warning=DEFAULT_OOD_THRESHOLDS["warning"],
            critical=DEFAULT_OOD_THRESHOLDS["critical"],
            version=THRESHOLD_VERSION)

    def feature_drift(self, *, model_hash: str, feature_key: str,
                      reference: Sequence[float], current: Sequence[float],
                      created_at: datetime,
                      environment: str = "RESEARCH") -> DriftReport:
        statistic = None
        if reference and current:
            statistic = _psi(reference, current)
        status = self._thresholds.classify(statistic)
        report = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash=model_hash,
            drift_type=DriftType.FEATURE_DRIFT,
            status=status,
            threshold_version=self._thresholds.version,
            created_at=ensure_utc(created_at, location="drift.created_at"),
            environment=environment,
            statistic=statistic,
            evidence={"feature_key": feature_key,
                      "reference_n": len(reference),
                      "current_n": len(current)},
        )
        report.validate()
        return report

    def prediction_drift(self, *, model_hash: str,
                         reference: Sequence[float],
                         current: Sequence[float],
                         created_at: datetime,
                         environment: str = "RESEARCH") -> DriftReport:
        statistic = None
        if reference and current:
            statistic = _psi(reference, current)
        report = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash=model_hash,
            drift_type=DriftType.PREDICTION_DRIFT,
            status=self._thresholds.classify(statistic),
            threshold_version=self._thresholds.version,
            created_at=ensure_utc(created_at, location="drift.created_at"),
            environment=environment,
            statistic=statistic,
            evidence={"reference_n": len(reference),
                      "current_n": len(current)},
        )
        report.validate()
        return report

    def performance_drift(self, *, model_hash: str,
                          reference_metric: float | None,
                          current_metric: float | None,
                          metric_name: str, created_at: datetime,
                          environment: str = "RESEARCH") -> DriftReport:
        """Performance drift compares versioned metric windows. UNKNOWN
        metrics stay UNKNOWN (never zero)."""
        if reference_metric is None or current_metric is None:
            statistic = None
        else:
            statistic = abs(reference_metric - current_metric)
        report = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash=model_hash,
            drift_type=DriftType.PERFORMANCE_DRIFT,
            status=self._thresholds.classify(statistic),
            threshold_version=self._thresholds.version,
            created_at=ensure_utc(created_at, location="drift.created_at"),
            environment=environment,
            statistic=statistic,
            evidence={"metric": metric_name,
                      "reference": reference_metric,
                      "current": current_metric},
        )
        report.validate()
        return report

    def label_drift(self, *, model_hash: str,
                    reference: Sequence[float], current: Sequence[float],
                    created_at: datetime,
                    environment: str = "RESEARCH") -> DriftReport:
        statistic = _psi(reference, current) if reference and current \
            else None
        report = DriftReport(
            drift_report_id=new_identifier("drift_report_id"),
            model_hash=model_hash,
            drift_type=DriftType.LABEL_DRIFT,
            status=self._thresholds.classify(statistic),
            threshold_version=self._thresholds.version,
            created_at=ensure_utc(created_at, location="drift.created_at"),
            environment=environment,
            statistic=statistic,
            evidence={"reference_n": len(reference),
                      "current_n": len(current)},
        )
        report.validate()
        return report

    def ood(self, *, model_hash: str, training: Sequence[float],
            value: float) -> OODResult:
        """Distance of one value from the training distribution, in units
        of training std. UNKNOWN when the training stats are degenerate."""
        if len(training) < 2:
            result = OODResult(model_hash=model_hash, status=OODStatus.UNKNOWN,
                               threshold_version=self._ood.version,
                               distance=None,
                               evidence={"reason": "insufficient training "
                                                   "sample"})
            result.validate()
            return result
        mu = _mean(training)
        sigma = _std(training)
        if sigma <= 0:
            result = OODResult(model_hash=model_hash, status=OODStatus.UNKNOWN,
                               threshold_version=self._ood.version,
                               distance=None,
                               evidence={"reason": "degenerate variance"})
            result.validate()
            return result
        distance = abs(value - mu) / sigma
        if distance >= self._ood.critical:
            status = OODStatus.OOD_CRITICAL
        elif distance >= self._ood.warning:
            status = OODStatus.OOD_WARNING
        else:
            status = OODStatus.IN_DISTRIBUTION
        result = OODResult(model_hash=model_hash, status=status,
                           threshold_version=self._ood.version,
                           distance=distance,
                           evidence={"training_mean": mu, "training_std": sigma})
        result.validate()
        return result


def drift_worst(reports: Sequence[DriftReport]) -> DriftStatus:
    """Aggregate drift health: worst of the components; UNKNOWN wins over
    NORMAL (fail closed) but loses to CRITICAL."""
    if not reports:
        return DriftStatus.UNKNOWN
    severities = {DriftStatus.NORMAL: 0, DriftStatus.UNKNOWN: 1,
                  DriftStatus.WARNING: 2, DriftStatus.CRITICAL: 3}
    worst = max(reports, key=lambda r: severities[r.status])
    return worst.status
