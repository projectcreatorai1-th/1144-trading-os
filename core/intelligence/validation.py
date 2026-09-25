"""Model validation gate (owned by core.intelligence).

SECTION 40: a model cannot become deployable merely because training
succeeded, accuracy is high or a backtest is profitable. The gate requires
the full evidence checklist; a missing or UNKNOWN critical item forces
overall UNKNOWN, and critical UNKNOWN means BLOCK.
"""
from __future__ import annotations

from datetime import datetime

from architecture.contracts.errors import ContractError
from architecture.contracts.identifiers import new_identifier
from architecture.contracts.time import ensure_utc

from core.intelligence.contracts import (
    CRITICAL_EVIDENCE,
    EvidenceStatus,
    ModelValidation,
)

CONTRACT_VERSION = "1.0.0"


class ValidationGateError(ContractError):
    rule_id = "MVAL"


def validation_gate(*, model_hash: str, evidence: dict[str, str],
                    auditor: str, created_at: datetime,
                    environment: str = "RESEARCH",
                    notes: str = "") -> ModelValidation:
    """Assess the evidence checklist. PRESENT everywhere -> PRESENT;
    any MISSING critical -> FAILED; any UNKNOWN critical -> UNKNOWN."""
    complete = {item: evidence.get(item, "UNKNOWN")
                for item in CRITICAL_EVIDENCE}
    for item in CRITICAL_EVIDENCE:
        if complete[item] not in ("PRESENT", "MISSING", "UNKNOWN"):
            raise ValidationGateError(
                f"evidence['{item}'] must be PRESENT/MISSING/UNKNOWN",
                location="gate.evidence", rule_id="MVAL-003",
                details={"item": item})
    if all(complete[item] == "PRESENT" for item in CRITICAL_EVIDENCE):
        overall = EvidenceStatus.PRESENT
    elif any(complete[item] == "UNKNOWN" for item in CRITICAL_EVIDENCE):
        overall = EvidenceStatus.UNKNOWN
    else:
        overall = EvidenceStatus.MISSING
    validation = ModelValidation(
        model_validation_id=new_identifier("model_validation_id"),
        model_hash=model_hash,
        evidence=complete,
        overall=overall,
        auditor=auditor,
        created_at=ensure_utc(created_at, location="gate.created_at"),
        environment=environment,
        notes=notes,
    )
    validation.validate()
    return validation


def gate_blocks(validation: ModelValidation) -> bool:
    """Critical UNKNOWN blocks (SECTION 40): only full PRESENT evidence
    opens the promotion path."""
    return validation.overall is not EvidenceStatus.PRESENT
