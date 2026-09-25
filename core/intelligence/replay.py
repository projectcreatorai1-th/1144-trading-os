"""Intelligence replay + diff (owned by core.intelligence).

SECTION 44/45/82/83: replay reproduces feature snapshots, model versions,
inference and outputs read-only in the REPLAY environment; it never
submits orders and never mutates production state. The diff engine
distinguishes EXPECTED_VERSION_DIFFERENCE from NONDETERMINISM_SUSPECTED.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.adapter import ModelAdapter, ModelAdapterError
from core.intelligence.contracts import (
    FeatureDefinition,
    ModelDefinition,
    canonical_hash,
)
from core.intelligence.feature import FeatureEngine
from core.intelligence.outputs import (
    ModelReplay,
    ReplayDiffKind,
    ReplayMatch,
)
from core.research.contracts import ResearchDataset

CONTRACT_VERSION = "1.0.0"


class ReplayError(ContractError):
    rule_id = "MREP"


class IntelligenceReplayAdapter:
    """Read-only replay: same dataset + feature versions + model version +
    config + seed -> same semantic outputs."""

    def __init__(self, engine: FeatureEngine, adapter: ModelAdapter) -> None:
        self._engine = engine
        self._adapter = adapter

    def replay(self, *, model: ModelDefinition, source: ResearchDataset,
               definitions: Sequence[FeatureDefinition], symbol: str,
               moments: Sequence[datetime],
               original_outputs: Sequence[Mapping[str, Any]],
               config_hash: str, created_at: datetime) -> ModelReplay:
        if not moments or len(moments) != len(original_outputs):
            raise ReplayError(
                "replay requires aligned moments and original outputs",
                location="replay.align", rule_id="MREP-002")
        reproduced_hashes: list[str] = []
        original_hashes: list[str] = []
        for moment, original in zip(moments, original_outputs):
            snapshot = self._engine.snapshot(
                dataset=source, definitions=definitions, symbol=symbol,
                as_of=ensure_utc(moment), environment="REPLAY")
            artifact_json = json.dumps(model.provenance.get("artifact", {}),
                                       sort_keys=True)
            try:
                output = self._adapter.predict(artifact_json, snapshot.values)
            except ModelAdapterError as error:
                raise ReplayError(
                    f"replay inference failed: {error}",
                    location="replay.infer", rule_id="MREP-003") from error
            reproduced_hashes.append(canonical_hash(output))
            original_hashes.append(canonical_hash(original))
        match = ReplayMatch.MATCH if reproduced_hashes == original_hashes \
            else ReplayMatch.MISMATCH
        replay = ModelReplay(
            model_replay_id=new_identifier("model_replay_id"),
            model_hash=model.model_hash,
            dataset_hash=source.content_hash,
            feature_schema_hash=model.feature_schema_hash,
            config_hash=config_hash,
            seed=model.seed,
            original_hashes=tuple(original_hashes),
            reproduced_hashes=tuple(reproduced_hashes),
            match=match,
            created_at=ensure_utc(created_at, location="replay.created_at"),
            environment="REPLAY",
            differences={} if match is ReplayMatch.MATCH else {
                str(i): {"original": original_hashes[i],
                         "reproduced": reproduced_hashes[i]}
                for i in range(len(original_hashes))
                if original_hashes[i] != reproduced_hashes[i]
            },
        )
        replay.validate()
        return replay


@dataclass(frozen=True)
class OutputDiff:
    kind: ReplayDiffKind
    field: str
    left: Any
    right: Any


def diff_inferences(left: Sequence[Mapping[str, Any]],
                    right: Sequence[Mapping[str, Any]],
                    *, left_model_hash: str, right_model_hash: str,
                    fields: Sequence[str] = ("prediction", "label",
                                             "probability", "confidence",
                                             "uncertainty")) -> list[OutputDiff]:
    """Compare two inference sets. Different model hashes make differences
    EXPECTED_VERSION_DIFFERENCE; identical hashes with different outputs
    mean NONDETERMINISM_SUSPECTED (SECTION 45/82)."""
    if len(left) != len(right):
        raise ReplayError(
            "diff requires equal-length sequences",
            location="diff.align", rule_id="MREP-004")
    kind = (ReplayDiffKind.EXPECTED_VERSION_DIFFERENCE
            if left_model_hash != right_model_hash
            else ReplayDiffKind.NONDETERMINISM_SUSPECTED)
    diffs: list[OutputDiff] = []
    for index, (l, r) in enumerate(zip(left, right)):
        for field_name in fields:
            lv = l.get(field_name)
            rv = r.get(field_name)
            if isinstance(lv, float) and isinstance(rv, float):
                if abs(lv - rv) > 1e-12:
                    diffs.append(OutputDiff(kind, f"[{index}].{field_name}",
                                            lv, rv))
            elif lv != rv:
                diffs.append(OutputDiff(kind, f"[{index}].{field_name}",
                                        lv, rv))
    return diffs


def compare_models(*, left_outputs: Sequence[Mapping[str, Any]],
                   right_outputs: Sequence[Mapping[str, Any]],
                   left_model_hash: str, right_model_hash: str) \
        -> Mapping[str, Any]:
    """SECTION 46/121: model regression/comparison produces EVIDENCE for
    human review. It never picks a winner."""
    diffs = diff_inferences(left_outputs, right_outputs,
                            left_model_hash=left_model_hash,
                            right_model_hash=right_model_hash)
    return {
        "left_model_hash": left_model_hash,
        "right_model_hash": right_model_hash,
        "n_compared": len(left_outputs),
        "n_differences": len(diffs),
        "same_model": left_model_hash == right_model_hash,
        "classification": (ReplayDiffKind.NONDETERMINISM_SUSPECTED.value
                           if left_model_hash == right_model_hash
                           else ReplayDiffKind.EXPECTED_VERSION_DIFFERENCE.value),
        "differences": [
            {"field": d.field, "left": d.left, "right": d.right}
            for d in diffs],
        "decision": "HUMAN_REVIEW_REQUIRED (no automatic winner)",
    }
