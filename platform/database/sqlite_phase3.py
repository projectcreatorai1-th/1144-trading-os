"""SQLite storage for Phase 3 (owned by platform.database).

Policy documents (immutable versions) and policy evaluations. Append-only;
policy_id+version is unique so semantic changes must create new versions."""
from __future__ import annotations

import json
import sqlite3
from typing import Iterator

from architecture.contracts.errors import StorageError
from architecture.contracts.time import canonical, ensure_utc, parse_canonical
from core.policy.contracts import Policy
from core.policy.evaluation import PolicyEvaluation
from core.policy.store import PolicyEvaluationStore, PolicyStore
from core.risk.context import RiskContext, build_context
from core.risk.contracts import RiskDecision, RiskResult, RiskState, MarketState
from core.risk.decision_store import RiskDecisionStore

CONTRACT_VERSION = "1.0.0"


class SqlitePolicyStore(PolicyStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def save(self, policy: Policy) -> None:
        policy.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO policies (policy_id, policy_version, policy_type, status,"
                    " scope, priority, environment, content, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        policy.policy_id, policy.policy_version, policy.policy_type.value,
                        policy.status.value, policy.scope, policy.priority, policy.environment,
                        json.dumps(policy.to_dict(), sort_keys=True, ensure_ascii=False),
                        canonical(policy.created_at), canonical(policy.updated_at),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Policy {policy.policy_id}@{policy.policy_version} already exists "
                "(versions are immutable - semantic changes create a new version)",
                location="sqlite.policy.save",
                details={"policy_id": policy.policy_id, "version": policy.policy_version},
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(
                f"Policy store write failure: {exc}", location="sqlite.policy.save"
            ) from exc

    def get_version(self, policy_id: str, policy_version: str) -> Policy:
        row = self._db.execute(
            "SELECT content FROM policies WHERE policy_id = ? AND policy_version = ?",
            (policy_id, policy_version),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Policy '{policy_id}@{policy_version}' not found",
                location="sqlite.policy.get_version",
            )
        return Policy.from_storage(json.loads(row[0]))

    def iter_versions(self, policy_id: str) -> Iterator[Policy]:
        for row in self._db.execute(
            "SELECT content FROM policies WHERE policy_id = ? ORDER BY seq", (policy_id,)
        ):
            yield Policy.from_storage(json.loads(row[0]))

    def iter_all_latest(self) -> Iterator[Policy]:
        for row in self._db.execute(
            "SELECT content FROM policies WHERE seq IN"
            " (SELECT MAX(seq) FROM policies GROUP BY policy_id) ORDER BY seq"
        ):
            yield Policy.from_storage(json.loads(row[0]))

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM policies").fetchone()[0])


class SqlitePolicyEvaluationStore(PolicyEvaluationStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, evaluation: PolicyEvaluation) -> None:
        evaluation.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO policy_evaluations (evaluation_id, policy_id, policy_version,"
                    " result, timestamp, environment, correlation_id, content)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        evaluation.evaluation_id, evaluation.policy_id,
                        evaluation.policy_version, evaluation.result,
                        canonical(evaluation.timestamp), evaluation.environment,
                        evaluation.correlation_id,
                        json.dumps({
                            "evaluation_id": evaluation.evaluation_id,
                            "policy_id": evaluation.policy_id,
                            "policy_version": evaluation.policy_version,
                            "result": evaluation.result,
                            "triggered_rules": [dict(r) for r in evaluation.triggered_rules],
                            "failed_rules": list(evaluation.failed_rules),
                            "warnings": list(evaluation.warnings),
                            "context_hash": evaluation.context_hash,
                            "policy_hash": evaluation.policy_hash,
                            "timestamp": canonical(evaluation.timestamp),
                            "environment": evaluation.environment,
                            "correlation_id": evaluation.correlation_id,
                            "causation_id": evaluation.causation_id,
                            "schema_version": evaluation.schema_version,
                        }, sort_keys=True, ensure_ascii=False),
                    ),
                )
        except sqlite3.Error as exc:
            raise StorageError(
                f"Policy evaluation store write failure: {exc}",
                location="sqlite.policy_evaluation.append",
            ) from exc

    def get_by_id(self, evaluation_id: str) -> PolicyEvaluation:
        row = self._db.execute(
            "SELECT content FROM policy_evaluations WHERE evaluation_id = ?",
            (evaluation_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Policy evaluation '{evaluation_id}' not found",
                location="sqlite.policy_evaluation.get_by_id",
            )
        data = json.loads(row[0])
        from architecture.contracts.time import parse_canonical

        evaluation = PolicyEvaluation(
            evaluation_id=data["evaluation_id"],
            policy_id=data["policy_id"],
            policy_version=data["policy_version"],
            result=data["result"],
            triggered_rules=tuple(dict(r) for r in data["triggered_rules"]),
            failed_rules=tuple(data["failed_rules"]),
            warnings=tuple(data["warnings"]),
            context_hash=data["context_hash"],
            policy_hash=data["policy_hash"],
            timestamp=parse_canonical(data["timestamp"]),
            environment=data["environment"],
            correlation_id=data["correlation_id"],
            causation_id=data.get("causation_id"),
            schema_version=data.get("schema_version", "1.0.0"),
        )
        evaluation.validate()
        return evaluation

    def iter_by_correlation_id(self, correlation_id: str) -> Iterator[PolicyEvaluation]:
        for row in self._db.execute(
            "SELECT evaluation_id FROM policy_evaluations WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self.get_by_id(row[0])

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM policy_evaluations").fetchone()[0])


class SqliteRiskDecisionStore(RiskDecisionStore):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def append(self, decision: RiskDecision, context: RiskContext) -> None:
        decision.validate()
        context.validate()
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO risk_decisions (risk_decision_id, environment, permission,"
                    " decision_time, correlation_id, decision_content, context_content)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        decision.risk_decision_id, decision.environment,
                        decision.decision.value, canonical(decision.decision_time),
                        decision.correlation_id,
                        json.dumps(decision.to_dict(), sort_keys=True, ensure_ascii=False),
                        json.dumps(context.to_content(), sort_keys=True, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Risk decision {decision.risk_decision_id} already stored (append-only)",
                location="sqlite.risk_decision.append",
            ) from exc
        except sqlite3.Error as exc:
            raise StorageError(
                f"Risk decision store write failure: {exc}",
                location="sqlite.risk_decision.append",
            ) from exc

    def get_by_id(self, risk_decision_id: str) -> RiskDecision:
        row = self._db.execute(
            "SELECT decision_content FROM risk_decisions WHERE risk_decision_id = ?",
            (risk_decision_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Risk decision '{risk_decision_id}' not found",
                location="sqlite.risk_decision.get_by_id",
            )
        return RiskDecision.from_storage(json.loads(row[0]))

    def get_context(self, risk_decision_id: str) -> RiskContext:
        row = self._db.execute(
            "SELECT context_content FROM risk_decisions WHERE risk_decision_id = ?",
            (risk_decision_id,),
        ).fetchone()
        if row is None:
            raise StorageError(
                f"Risk decision '{risk_decision_id}' not found",
                location="sqlite.risk_decision.get_context",
            )
        return _context_from_content(json.loads(row[0]))

    def iter_by_correlation_id(self, correlation_id: str):
        for row in self._db.execute(
            "SELECT risk_decision_id FROM risk_decisions WHERE correlation_id = ? ORDER BY seq",
            (correlation_id,),
        ):
            yield self.get_by_id(row[0])

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0])


def _context_from_content(content: dict) -> RiskContext:
    as_of = parse_canonical(content["as_of"])
    environment = content["environment"]
    fields = {k: v for k, v in content.items() if k not in ("as_of", "environment")}
    return build_context(as_of=as_of, environment=environment, **fields)
