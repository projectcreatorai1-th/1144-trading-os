"""Research contracts (owned by core.research).

Research plane: datasets (point-in-time correct), versioned configs and
execution models, deterministic research runs, results, candidates, bias and
stress reports. Research NEVER executes externally and NEVER auto-promotes
strategies (SECTION 1-2/83/119)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Tuple

from architecture.contracts.environment import parse_environment
from architecture.contracts.errors import ContractValidationError
from architecture.contracts.identifiers import new_identifier, validate_identifier
from architecture.contracts.time import ensure_utc
from architecture.contracts.versioning import SemVer
from core.ledger.money import parse_decimal

CONTRACT_VERSION = "1.0.0"
RESEARCH_ENVIRONMENTS = frozenset({"RESEARCH", "BACKTEST", "REPLAY"})


def canonical_hash(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _require_research_environment(environment: str, location: str) -> None:
    parse_environment(environment, location=location)
    if environment not in RESEARCH_ENVIRONMENTS:
        raise ContractValidationError(
            f"{location} must be a research-plane environment "
            f"({sorted(RESEARCH_ENVIRONMENTS)}); research objects never execute "
            "in SIMULATION/PAPER/DEMO/LIVE",
            location=location, rule_id="ENV-005",
            details={"value": environment},
        )


# --------------------------------------------------------------------- #
# Dataset                                                                #
# --------------------------------------------------------------------- #
@dataclass(frozen=True)
class Observation:
    """One point-in-time observation: what was known, when it became known."""
    symbol: str
    event_time: datetime
    available_time: datetime
    payload: Mapping[str, Any]
    data_type: str = "MARKET_DATA"

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ContractValidationError(
                "observation.symbol must be a non-empty string",
                location="observation.symbol")
        ensure_utc(self.event_time, location="observation.event_time")
        ensure_utc(self.available_time, location="observation.available_time")
        if self.available_time < self.event_time:
            raise ContractValidationError(
                f"observation.available_time before event_time for {self.symbol} "
                "(availability cannot precede the event)",
                location="observation.available_time", rule_id="PIT-001")
        if not isinstance(self.payload, Mapping):
            raise ContractValidationError(
                "observation.payload must be a mapping",
                location="observation.payload")


@dataclass(frozen=True)
class ResearchDataset:
    dataset_id: str
    dataset_version: str
    symbols: Tuple[str, ...]
    time_range: Mapping[str, str]
    timeframe: str
    timezone: str
    data_source: str
    source_versions: Mapping[str, str]
    quality_summary: Mapping[str, Any]
    lineage: Mapping[str, Any]
    content_hash: str
    created_at: datetime
    environment: str
    observations: Tuple[Observation, ...] = ()
    historical_membership: Mapping[str, Any] | None = None
    session_calendar: Mapping[str, Any] | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("dataset_id", self.dataset_id, location="dataset.dataset_id")
        SemVer.parse(self.dataset_version, location="dataset.dataset_version")
        if not self.symbols or not all(isinstance(s, str) and s for s in self.symbols):
            raise ContractValidationError(
                "dataset.symbols must be a non-empty tuple of strings",
                location="dataset.symbols")
        if "start" not in self.time_range or "end" not in self.time_range:
            raise ContractValidationError(
                "dataset.time_range must contain start and end",
                location="dataset.time_range")
        for key in ("start", "end"):
            ensure_utc(datetime.fromisoformat(self.time_range[key]),
                       location=f"dataset.time_range.{key}")
        if not isinstance(self.timeframe, str) or not self.timeframe:
            raise ContractValidationError(
                "dataset.timeframe must be a non-empty string",
                location="dataset.timeframe")
        if not isinstance(self.timezone, str) or not self.timezone:
            raise ContractValidationError(
                "dataset.timezone must be explicit (missing timezone is UNKNOWN -> invalid)",
                location="dataset.timezone")
        for name in ("data_source", "source_versions", "quality_summary", "lineage"):
            value = getattr(self, name)
            if not value or (isinstance(value, Mapping) and not isinstance(value, str) and not value):
                raise ContractValidationError(
                    f"dataset.{name} must be provided (Phase 1 lineage/quality cannot be skipped)",
                    location=f"dataset.{name}")
        expected = self.compute_content_hash()
        if self.content_hash != expected:
            raise ContractValidationError(
                "dataset.content_hash mismatch (corrupted dataset rejected)",
                location="dataset.content_hash", rule_id="DATASET-003",
                details={"expected": expected})
        _require_research_environment(self.environment, "dataset.environment")
        for observation in self.observations:
            observation.validate()
        ensure_utc(self.created_at, location="dataset.created_at")

    def compute_content_hash(self) -> str:
        return canonical_hash({
            "symbols": list(self.symbols),
            "time_range": dict(self.time_range), "timeframe": self.timeframe,
            "observations": [
                {"symbol": o.symbol,
                 "event_time": ensure_utc(o.event_time).isoformat(),
                 "available_time": ensure_utc(o.available_time).isoformat(),
                 "payload": dict(o.payload), "data_type": o.data_type}
                for o in self.observations
            ],
        })

    def visible_at(self, moment: datetime) -> tuple:
        """Point-in-time view: ONLY observations available at `moment`
        (SECTION 9 - an event at 10:00 available 10:02 is invisible at 10:01)."""
        at = ensure_utc(moment, location="dataset.visible_at")
        return tuple(
            o for o in self.observations
            if ensure_utc(o.available_time) <= at
        )

    @property
    def survivorship_status(self) -> str:
        """UNKNOWN without historical membership (SECTION 12)."""
        if self.historical_membership is None:
            return "UNKNOWN"
        return "KNOWN"


# --------------------------------------------------------------------- #
# Config / execution model                                               #
# --------------------------------------------------------------------- #
class CapitalMode(Enum):
    FIXED_CAPITAL = "FIXED_CAPITAL"
    DYNAMIC_EQUITY = "DYNAMIC_EQUITY"
    COMPOUNDING = "COMPOUNDING"


@dataclass(frozen=True)
class ResearchConfig:
    config_id: str
    config_version: str
    initial_capital: str
    symbols: Tuple[str, ...]
    timeframe: str
    date_range: Mapping[str, str]
    commission: str
    spread: str
    slippage: str
    leverage: str
    execution_assumptions: Mapping[str, Any]
    config_hash: str
    environment: str
    capital_mode: str = "FIXED_CAPITAL"
    swap: str | None = None
    margin_assumptions: Mapping[str, Any] | None = None
    session_rules: Mapping[str, Any] | None = None
    position_sizing: Mapping[str, Any] | None = None
    seed: int | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("config_id", self.config_id, location="config.config_id")
        SemVer.parse(self.config_version, location="config.config_version")
        parse_decimal(self.initial_capital, location="config.initial_capital")
        if not self.symbols:
            raise ContractValidationError(
                "config.symbols must be non-empty", location="config.symbols")
        # SECTION 21/115: costs must be explicit - UNKNOWN not implicit zero
        for name in ("commission", "spread", "slippage", "leverage"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise ContractValidationError(
                    f"config.{name} must be an explicit value or 'UNKNOWN' "
                    "(never implicit zero)",
                    location=f"config.{name}")
        if self.capital_mode not in ("FIXED_CAPITAL", "DYNAMIC_EQUITY", "COMPOUNDING"):
            raise ContractValidationError(
                f"config.capital_mode invalid: {self.capital_mode}",
                location="config.capital_mode", rule_id="SCHEMA-ENUM")
        expected = self.compute_config_hash()
        if self.config_hash != expected:
            raise ContractValidationError(
                "config.config_hash mismatch",
                location="config.config_hash", rule_id="CONFIG-001")
        _require_research_environment(self.environment, "config.environment")

    def compute_config_hash(self) -> str:
        return canonical_hash({
            "symbols": list(self.symbols),
            "timeframe": self.timeframe, "date_range": dict(self.date_range),
            "commission": self.commission, "spread": self.spread,
            "slippage": self.slippage, "leverage": self.leverage,
            "capital_mode": self.capital_mode, "swap": self.swap,
            "seed": self.seed,
        })


class SpreadModel(Enum):
    FIXED = "FIXED"
    HISTORICAL = "HISTORICAL"
    VARIABLE = "VARIABLE"
    UNKNOWN = "UNKNOWN"


class IntrabarPolicy(Enum):
    CONSERVATIVE = "CONSERVATIVE"
    DETERMINISTIC = "DETERMINISTIC"
    TICK_REQUIRED = "TICK_REQUIRED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExecutionModel:
    execution_model_id: str
    execution_model_version: str
    fill_models: Mapping[str, Any]
    spread_model: SpreadModel
    slippage_model: Mapping[str, Any]
    latency_assumptions: Mapping[str, Any]
    commission_model: Mapping[str, Any]
    liquidity_assumption: str
    market_impact: str
    intrabar_policy: IntrabarPolicy
    model_hash: str
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("execution_model_id", self.execution_model_id,
                            location="model.execution_model_id")
        SemVer.parse(self.execution_model_version, location="model.execution_model_version")
        if not self.fill_models:
            raise ContractValidationError(
                "model.fill_models must declare explicit rules per order type",
                location="model.fill_models")
        if not isinstance(self.spread_model, SpreadModel):
            raise ContractValidationError(
                "model.spread_model must be a SpreadModel",
                location="model.spread_model", rule_id="SCHEMA-ENUM")
        if not isinstance(self.intrabar_policy, IntrabarPolicy):
            raise ContractValidationError(
                "model.intrabar_policy must be an IntrabarPolicy",
                location="model.intrabar_policy", rule_id="SCHEMA-ENUM")
        for name in ("liquidity_assumption", "market_impact"):
            value = getattr(self, name)
            if value not in ("UNKNOWN", "ASSUMPTION") and not isinstance(value, str):
                raise ContractValidationError(
                    f"model.{name} must be explicit",
                    location=f"model.{name}")
        expected = self.compute_model_hash()
        if self.model_hash != expected:
            raise ContractValidationError(
                "model.model_hash mismatch",
                location="model.model_hash", rule_id="MODEL-001")
        _require_research_environment(self.environment, "model.environment")

    def compute_model_hash(self) -> str:
        return canonical_hash({
            "fill_models": dict(self.fill_models),
            "spread_model": self.spread_model.value,
            "slippage_model": dict(self.slippage_model),
            "latency": dict(self.latency_assumptions),
            "commission": dict(self.commission_model),
            "liquidity": self.liquidity_assumption,
            "market_impact": self.market_impact,
            "intrabar_policy": self.intrabar_policy.value,
        })


# --------------------------------------------------------------------- #
# Research run / result / candidate / reports                            #
# --------------------------------------------------------------------- #
def research_run_hash(*, dataset_id: str, dataset_hash: str, strategy_id: str,
                      strategy_hash: str, config_hash: str, execution_model_hash: str,
                      policy_hash: str, code_version: str, seed: int | None) -> str:
    """Deterministic semantic identity (SECTION 5): same inputs + same
    versions + same data = same research identity."""
    return canonical_hash({
        "dataset_hash": dataset_hash,
        "strategy_hash": strategy_hash,
        "config_hash": config_hash, "execution_model_hash": execution_model_hash,
        "policy_hash": policy_hash, "code_version": code_version, "seed": seed,
    })


@dataclass(frozen=True)
class ResearchRun:
    research_run_id: str
    dataset_id: str
    dataset_version: str
    dataset_hash: str
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    config_id: str
    config_version: str
    config_hash: str
    execution_model_id: str
    execution_model_version: str
    execution_model_hash: str
    policy_reference: str
    policy_hash: str
    portfolio_version: str
    portfolio_hash: str
    environment: str
    created_at: datetime
    source_hash: str
    input_hash: str
    code_version: str
    run_hash: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    result_hash: str | None = None
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("research_run_id", self.research_run_id,
                            location="run.research_run_id")
        validate_identifier("dataset_id", self.dataset_id, location="run.dataset_id")
        validate_identifier("strategy_id", self.strategy_id, location="run.strategy_id")
        validate_identifier("config_id", self.config_id, location="run.config_id")
        validate_identifier("execution_model_id", self.execution_model_id,
                            location="run.execution_model_id")
        SemVer.parse(self.dataset_version, location="run.dataset_version")
        SemVer.parse(self.strategy_version, location="run.strategy_version")
        for name in ("dataset_hash", "strategy_hash", "config_hash",
                     "execution_model_hash", "policy_hash", "portfolio_hash",
                     "source_hash", "input_hash", "run_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64:
                raise ContractValidationError(
                    f"run.{name} must be a sha-256 hex string",
                    location=f"run.{name}")
        if self.result_hash is not None and len(self.result_hash) != 64:
            raise ContractValidationError(
                "run.result_hash must be a sha-256 hex string",
                location="run.result_hash")
        _require_research_environment(self.environment, "run.environment")
        ensure_utc(self.created_at, location="run.created_at")

    def verify_dependencies(self, *, dataset: ResearchDataset,
                            config: ResearchConfig,
                            model: ExecutionModel) -> tuple[str, ...]:
        """SECTION 19/97: hash verification against the locked dependencies."""
        mismatches = []
        if dataset.content_hash != self.dataset_hash:
            mismatches.append("dataset_hash")
        if config.config_hash != self.config_hash:
            mismatches.append("config_hash")
        if model.model_hash != self.execution_model_hash:
            mismatches.append("execution_model_hash")
        return tuple(mismatches)


class ResultStatus(Enum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"
    INCONCLUSIVE = "INCONCLUSIVE"


class BiasStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class RobustnessStatus(Enum):
    STABLE = "STABLE"
    SENSITIVE = "SENSITIVE"
    UNSTABLE = "UNSTABLE"
    UNKNOWN = "UNKNOWN"


class ValidationStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ResearchResult:
    result_id: str
    research_run_id: str
    status: ResultStatus
    metrics: Mapping[str, Any]
    risk_metrics: Mapping[str, Any]
    bias_status: BiasStatus
    data_quality: str
    robustness_status: RobustnessStatus
    validation_status: ValidationStatus
    evidence_hash: str
    created_at: datetime
    environment: str
    multiple_testing: bool
    assumption_labels: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("research_result_id", self.result_id,
                            location="result.result_id")
        validate_identifier("research_run_id", self.research_run_id,
                            location="result.research_run_id")
        for name, enum_type in (("status", ResultStatus), ("bias_status", BiasStatus),
                                ("robustness_status", RobustnessStatus),
                                ("validation_status", ValidationStatus)):
            if not isinstance(getattr(self, name), enum_type):
                raise ContractValidationError(
                    f"result.{name} must be a {enum_type.__name__}",
                    location=f"result.{name}", rule_id="SCHEMA-ENUM")
        if self.bias_status is BiasStatus.FAIL and self.status is ResultStatus.VALID:
            raise ContractValidationError(
                "Bias FAIL invalidates the result (UNKNOWN never becomes VALID)",
                location="result.status", rule_id="BIAS-001",
            )
        if len(self.evidence_hash) != 64:
            raise ContractValidationError(
                "result.evidence_hash must be a sha-256 hex string",
                location="result.evidence_hash")
        _require_research_environment(self.environment, "result.environment")
        ensure_utc(self.created_at, location="result.created_at")
        if not isinstance(self.multiple_testing, bool):
            raise ContractValidationError(
                "result.multiple_testing must be a boolean (data-snooping reporting)",
                location="result.multiple_testing")


class CandidateStatus(Enum):
    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    BACKTESTED = "BACKTESTED"
    VALIDATING = "VALIDATING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class StrategyCandidate:
    candidate_id: str
    strategy_family: str
    logic_version: str
    logic_hash: str
    parameter_set: Mapping[str, Any]
    dataset_reference: str
    research_run_reference: str
    status: CandidateStatus
    created_at: datetime
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("strategy_candidate_id", self.candidate_id,
                            location="candidate.candidate_id")
        if not isinstance(self.strategy_family, str) or not self.strategy_family:
            raise ContractValidationError(
                "candidate.strategy_family must be a non-empty string",
                location="candidate.strategy_family")
        SemVer.parse(self.logic_version, location="candidate.logic_version")
        if len(self.logic_hash) != 64:
            raise ContractValidationError(
                "candidate.logic_hash must be a sha-256 hex string",
                location="candidate.logic_hash")
        if not isinstance(self.parameter_set, Mapping):
            raise ContractValidationError(
                "candidate.parameter_set must be a mapping",
                location="candidate.parameter_set")
        if not isinstance(self.status, CandidateStatus):
            raise ContractValidationError(
                "candidate.status must be a CandidateStatus",
                location="candidate.status", rule_id="SCHEMA-ENUM")
        _require_research_environment(self.environment, "candidate.environment")
        ensure_utc(self.created_at, location="candidate.created_at")


@dataclass(frozen=True)
class BiasCheck:
    bias_type: str
    status: BiasStatus
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {"bias_type": self.bias_type,
                "status": self.status.value, "evidence": self.evidence}


@dataclass(frozen=True)
class BiasReport:
    bias_report_id: str
    research_run_id: str
    overall: BiasStatus
    checks: Tuple[BiasCheck, ...]
    evidence_hash: str
    created_at: datetime
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("bias_report_id", self.bias_report_id,
                            location="bias.bias_report_id")
        if not isinstance(self.overall, BiasStatus):
            raise ContractValidationError(
                "bias.overall must be a BiasStatus",
                location="bias.overall", rule_id="SCHEMA-ENUM")
        if not self.checks:
            raise ContractValidationError(
                "bias.checks must be non-empty",
                location="bias.checks")
        _require_research_environment(self.environment, "bias.environment")
        ensure_utc(self.created_at, location="bias.created_at")


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    parameter: str
    multiplier: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {"scenario_id": self.scenario_id, "parameter": self.parameter,
                "multiplier": self.multiplier, "description": self.description}


@dataclass(frozen=True)
class StressReport:
    stress_report_id: str
    research_run_id: str
    scenarios: Tuple[StressScenario, ...]
    overall: str
    config_version: str
    created_at: datetime
    environment: str
    schema_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        validate_identifier("stress_report_id", self.stress_report_id,
                            location="stress.stress_report_id")
        if not self.scenarios:
            raise ContractValidationError(
                "stress.scenarios must be non-empty (versioned parameters from config)",
                location="stress.scenarios")
        SemVer.parse(self.config_version, location="stress.config_version")
        _require_research_environment(self.environment, "stress.environment")
        ensure_utc(self.created_at, location="stress.created_at")
