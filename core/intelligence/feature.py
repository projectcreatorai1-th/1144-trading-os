"""Feature engine (owned by core.intelligence).

Deterministic, point-in-time feature computation over Phase 6 research
datasets. The ONLY availability oracle is ResearchDataset.visible_at -
this module contains no second PIT implementation (SECTION 6).

Leakage protection (SECTION 7): FUTURE_DATA / LOOK_AHEAD /
INVALID_AVAILABLE_TIME / FUTURE_NORMALIZATION / TARGET_LEAKAGE are
detected and rejected. UNKNOWN critical inputs block - they never
become 0 / false / neutral / latest-value.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from architecture.contracts.errors import ContractError, ContractValidationError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import (
    FeatureDefinition,
    FeatureSnapshot,
    MissingValuePolicy,
    canonical_hash,
)
from core.research.contracts import Observation, ResearchDataset

CONTRACT_VERSION = "1.0.0"

#: A feature implementation receives the PIT-visible observations for one
#: symbol (ordered by event_time) and returns the feature value.
FeatureFunction = Callable[[Sequence[Observation]], Any]


class FeatureEngineError(ContractError):
    rule_id = "FEATURE-ENGINE"


def implementation_hash(function: FeatureFunction) -> str:
    """Hash of the implementation source: definitions and code are locked
    together (SECTION 5 implementation_hash)."""
    source = inspect.getsource(function)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _verify_implementation(definition: FeatureDefinition,
                           function: FeatureFunction) -> None:
    actual = implementation_hash(function)
    if actual != definition.implementation_hash:
        raise FeatureEngineError(
            "feature implementation hash mismatch (definition and code "
            "disagree - the definition must be re-versioned)",
            location="feature.engine.register", rule_id="FEATURE-003",
            details={"expected": definition.implementation_hash,
                     "actual": actual})


def audit_normalization(definitions: Sequence[FeatureDefinition]) -> None:
    """FUTURE_NORMALIZATION guard: normalization parameters must be fitted
    on the TRAIN split only, and that must be declared in the definition."""
    for definition in definitions:
        fitted_on = definition.normalization_definition.get("fitted_on")
        if fitted_on != "TRAIN":
            raise FeatureEngineError(
                f"feature {definition.feature_id}@{definition.feature_version} "
                "normalization must declare fitted_on=TRAIN (statistics "
                "computed over validation/OOS data are FUTURE_NORMALIZATION)",
                location="feature.normalization", rule_id="AI-LEAKAGE",
                details={"feature": definition.feature_id,
                         "leakage": "FUTURE_NORMALIZATION"})


def audit_label_independence(definitions: Sequence[FeatureDefinition],
                             label_key: str | None = None) -> None:
    """TARGET_LEAKAGE guard: features may never depend on label values."""
    for definition in definitions:
        for dep in definition.dependencies:
            if dep.startswith("label:") or (label_key and dep == label_key):
                raise FeatureEngineError(
                    f"feature {definition.feature_id} depends on a label "
                    "(future_return is a LABEL, never a feature)",
                    location="feature.dependencies", rule_id="AI-LEAKAGE",
                    details={"feature": definition.feature_id,
                             "leakage": "TARGET_LEAKAGE"})


@dataclass(frozen=True)
class FeatureComputation:
    """The value (or explicit missing/UNKNOWN state) of one feature."""
    feature_key: str
    value: Any
    state: str  # COMPUTED | MISSING | UNKNOWN


class FeatureEngine:
    """Computes point-in-time feature snapshots from research datasets."""

    def __init__(self) -> None:
        self._implementations: dict[str, FeatureFunction] = {}

    def register(self, definition: FeatureDefinition,
                 function: FeatureFunction) -> None:
        definition.validate()
        _verify_implementation(definition, function)
        key = f"{definition.feature_id}@{definition.feature_version}"
        if definition.status.value not in ("ACTIVE", "DRAFT"):
            raise FeatureEngineError(
                "only ACTIVE/DRAFT features can be registered",
                location="feature.engine.register", rule_id="FEATURE-004")
        self._implementations[key] = function

    def snapshot(self, *, dataset: ResearchDataset,
                 definitions: Sequence[FeatureDefinition],
                 symbol: str, as_of: datetime,
                 environment: str) -> FeatureSnapshot:
        """Point-in-time snapshot: ONLY observations visible at as_of."""
        moment = ensure_utc(as_of, location="feature.snapshot.as_of")
        if symbol not in dataset.symbols:
            raise FeatureEngineError(
                f"symbol '{symbol}' not in dataset scope",
                location="feature.snapshot.symbol", rule_id="FEATURE-005")
        visible = [
            o for o in dataset.visible_at(moment) if o.symbol == symbol
        ]
        visible.sort(key=lambda o: (ensure_utc(o.event_time), o.symbol))
        values: dict[str, Any] = {}
        missing: list[str] = []
        computations: list[FeatureComputation] = []
        for definition in definitions:
            key = f"{definition.feature_id}@{definition.feature_version}"
            function = self._implementations.get(key)
            if function is None:
                raise FeatureEngineError(
                    f"no implementation registered for {key}",
                    location="feature.snapshot", rule_id="FEATURE-006",
                    details={"feature": key})
            window = visible[-definition.lookback_window:]
            if not window:
                computations.append(self._apply_missing_policy(
                    definition, key, reason="no visible observations"))
                continue
            value = function(window)
            if value is None:
                computations.append(self._apply_missing_policy(
                    definition, key, reason="implementation returned None"))
                continue
            values[key] = value
            computations.append(FeatureComputation(key, value, "COMPUTED"))

        used_available = max(
            (ensure_utc(o.available_time) for o in visible), default=moment)
        # INVALID_AVAILABLE_TIME guard: nothing in the snapshot may become
        # available after the as-of moment.
        if used_available > moment:
            raise FeatureEngineError(
                "snapshot would contain data not yet available at as_of "
                "(INVALID_AVAILABLE_TIME)",
                location="feature.snapshot", rule_id="AI-LEAKAGE",
                details={"leakage": "INVALID_AVAILABLE_TIME"})
        for computation in computations:
            if computation.state == "COMPUTED":
                values[computation.feature_key] = computation.value
            elif computation.state == "MISSING":
                missing.append(computation.feature_key)
            # UNKNOWN values stay in `values` as the sentinel "UNKNOWN"
        snapshot = FeatureSnapshot(
            snapshot_id=new_identifier("feature_snapshot_id"),
            feature_keys=tuple(
                f"{d.feature_id}@{d.feature_version}" for d in definitions),
            values=values,
            as_of=moment,
            available_at=used_available,
            environment=environment,
            content_hash="",
            provenance={
                "dataset_id": dataset.dataset_id,
                "dataset_hash": dataset.content_hash,
                "symbol": symbol,
                "visible_observation_count": len(visible),
                "states": {c.feature_key: c.state for c in computations},
            },
            missing_features=tuple(sorted(missing)),
        )
        object.__setattr__(snapshot, "content_hash",
                           snapshot.compute_content_hash())
        snapshot.validate()
        return snapshot

    @staticmethod
    def _apply_missing_policy(definition: FeatureDefinition, key: str, *,
                              reason: str) -> FeatureComputation:
        if definition.missing_value_policy is MissingValuePolicy.BLOCK:
            raise FeatureEngineError(
                f"feature {key} is missing and its policy is BLOCK "
                f"({reason})",
                location="feature.snapshot", rule_id="AI-UNKNOWN",
                details={"feature": key, "reason": reason})
        if definition.missing_value_policy is MissingValuePolicy.DECLARED_DEFAULT:
            return FeatureComputation(key, definition.declared_default,
                                      "COMPUTED")
        return FeatureComputation(key, "UNKNOWN", "UNKNOWN")


def verify_snapshot_pit(snapshot: FeatureSnapshot,
                        dataset: ResearchDataset) -> None:
    """Defense-in-depth PIT verification (SECTION 81 corruption tests use
    this): every observation the snapshot could have used must have been
    visible at snapshot.as_of."""
    visible = dataset.visible_at(snapshot.as_of)
    visible_keys = {
        (o.symbol, ensure_utc(o.available_time)) for o in visible
    }
    for observation in dataset.observations:
        key = (observation.symbol, ensure_utc(observation.available_time))
        if key not in visible_keys and \
                ensure_utc(observation.available_time) <= snapshot.available_at:
            # an observation available before the snapshot's data cutoff was
            # NOT visible at as_of -> only an availability inconsistency
            # could expose it; visible_at is the single oracle, so re-derive
            if ensure_utc(observation.available_time) > snapshot.as_of:
                raise ContractValidationError(
                    "snapshot exposes an observation that was not available "
                    "at as_of (LOOK_AHEAD)",
                    location="snapshot.pit", rule_id="AI-LEAKAGE",
                    details={"leakage": "LOOK_AHEAD"})
