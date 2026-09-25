"""Intelligence dataset builder + split integrity (owned by core.intelligence).

Builds immutable IntelligenceDatasets from Phase 6 research datasets through
deterministic feature computation. Split integrity (SECTION 10): TRAIN <
VALIDATION < OOS with no temporal inversion, no duplicate observations
across splits unless explicitly declared valid by contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import (
    IntelligenceDataset,
    IntelligenceDatasetType,
    LabelDefinition,
    SplitDefinition,
    canonical_hash,
)
from core.intelligence.feature import (
    FeatureEngine,
    audit_label_independence,
    audit_normalization,
)
from core.research.contracts import ResearchDataset

CONTRACT_VERSION = "1.0.0"


class DatasetBuildError(ContractError):
    rule_id = "DATASETX"


def _split_type_for_window(dataset_type: IntelligenceDatasetType) -> bool:
    return dataset_type in (
        IntelligenceDatasetType.TRAIN,
        IntelligenceDatasetType.VALIDATION,
        IntelligenceDatasetType.OOS,
    )


def validate_split_separation(splits: Sequence[IntelligenceDataset],
                              declared: SplitDefinition | None = None) -> None:
    """SECTION 10: reject train/validation/OOS contamination, temporal
    inversion and duplicate observations across splits."""
    if not splits:
        raise DatasetBuildError("no splits to validate",
                                location="split.validate")
    by_type = {split.dataset_type: split for split in splits}
    for required in (IntelligenceDatasetType.TRAIN,
                     IntelligenceDatasetType.VALIDATION,
                     IntelligenceDatasetType.OOS):
        if required not in by_type:
            raise DatasetBuildError(
                f"split family missing {required.value}",
                location="split.validate", rule_id="SPLIT-004")
    order = [IntelligenceDatasetType.TRAIN,
             IntelligenceDatasetType.VALIDATION,
             IntelligenceDatasetType.OOS]
    for earlier, later in zip(order, order[1:]):
        early_end = datetime.fromisoformat(by_type[earlier].time_range["end"])
        late_start = datetime.fromisoformat(by_type[later].time_range["start"])
        ensure_utc(early_end, location="split.early_end")
        ensure_utc(late_start, location="split.late_start")
        overlap = late_start <= early_end
        if overlap and not (declared and declared.overlap_declared):
            raise DatasetBuildError(
                f"{later.value} window overlaps {earlier.value} "
                "(cross-split contamination)",
                location="split.validate", rule_id="SPLIT-002",
                details={"leakage": "CROSS_SPLIT_LEAKAGE",
                         "earlier": earlier.value, "later": later.value})
    # duplicate rows across splits: identical row digests are contamination
    seen: set[str] = set()
    for split in splits:
        digest = split.provenance.get("row_digest", "")
        if not digest:
            continue
        if digest in seen:
            raise DatasetBuildError(
                "identical row digest appears in two splits "
                "(duplicate observations across splits)",
                location="split.validate", rule_id="SPLIT-005",
                details={"leakage": "CROSS_SPLIT_LEAKAGE"})
        seen.add(digest)


@dataclass(frozen=True)
class DatasetBuildResult:
    dataset: IntelligenceDataset
    rows: tuple[Mapping[str, Any], ...]


class IntelligenceDatasetBuilder:
    """Deterministic dataset builder: same source dataset + same feature
    versions + same window -> same content hash."""

    def __init__(self, engine: FeatureEngine) -> None:
        self._engine = engine

    def build(self, *, source: ResearchDataset,
              dataset_type: IntelligenceDatasetType,
              definitions: Sequence,
              split: SplitDefinition,
              symbol: str,
              normalization_version: str,
              environment: str = "RESEARCH",
              label: LabelDefinition | None = None,
              label_values: Mapping[str, Any] | None = None,
              start: datetime | None = None,
              end: datetime | None = None) -> DatasetBuildResult:
        audit_normalization(definitions)
        if label is not None:
            audit_label_independence(definitions, label_key="label")
        feature_schema_hash = canonical_hash(
            {f"{d.feature_id}@{d.feature_version}":
             d.content_hash for d in definitions})
        windows = self._windows(source, symbol)
        if not windows:
            raise DatasetBuildError(
                "no observations for symbol in source dataset",
                location="idataset.build", rule_id="DATASETX-002")
        lo = ensure_utc(start) if start else None
        hi = ensure_utc(end) if end else None
        rows: list[Mapping[str, Any]] = []
        for moment in windows:
            if lo is not None and moment < lo:
                continue
            if hi is not None and moment > hi:
                continue
            snapshot = self._engine.snapshot(
                dataset=source, definitions=definitions, symbol=symbol,
                as_of=moment, environment=environment)
            row: dict[str, Any] = {
                "as_of": snapshot.as_of.isoformat(),
                "features": dict(snapshot.values),
            }
            if label is not None and label_values is not None:
                key = snapshot.as_of.isoformat()
                row["label"] = label_values.get(key, "UNKNOWN")
            rows.append(row)
        if not rows:
            raise DatasetBuildError(
                "empty window selection produced zero rows",
                location="idataset.build", rule_id="DATASETX-002")
        row_digest = canonical_hash(rows)
        dataset = IntelligenceDataset(
            intelligence_dataset_id=new_identifier("intelligence_dataset_id"),
            dataset_version="1.0.0",
            dataset_type=dataset_type,
            feature_schema_hash=feature_schema_hash,
            source_dataset_hash=source.content_hash,
            observation_count=len(rows),
            time_range={
                "start": (rows[0]["as_of"]),
                "end": (rows[-1]["as_of"]),
            },
            symbol_scope=(symbol,),
            environment=environment,
            split_definition={
                "split_id": split.split_id,
                "semantics": split.semantics,
                "dataset_type": dataset_type.value,
            },
            normalization_version=normalization_version,
            provenance={
                "source_dataset_id": source.dataset_id,
                "source_dataset_hash": source.content_hash,
                "feature_definitions": {
                    f"{d.feature_id}@{d.feature_version}": d.content_hash
                    for d in definitions},
                "row_digest": row_digest,
                "label_definition_hash": label.content_hash
                if label else None,
            },
            content_hash="",
            created_at=windows[0],
            label_definition_hash=label.content_hash if label else None,
        )
        object.__setattr__(dataset, "content_hash",
                           dataset.compute_content_hash())
        dataset.validate()
        return DatasetBuildResult(dataset=dataset, rows=tuple(rows))

    @staticmethod
    def _windows(source: ResearchDataset, symbol: str) -> list[datetime]:
        moments = sorted({
            ensure_utc(o.available_time) for o in source.observations
            if o.symbol == symbol
        })
        return moments


def label_available_at(event_time: datetime, horizon_bars: int,
                       bar_seconds: int) -> datetime:
    """SECTION 11: a label only exists after its horizon passes; anything
    earlier is TARGET_LEAKAGE."""
    from datetime import timedelta
    ensured = ensure_utc(event_time, location="label.availability")
    return ensured + timedelta(seconds=horizon_bars * bar_seconds)
