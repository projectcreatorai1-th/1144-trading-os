"""Phase 8 benchmark (SECTION 53). LOCAL DEVELOPMENT BENCHMARK ONLY.

SYNTHETIC / LOCAL / NON-PRODUCTION: synthetic credentials, in-memory
stores, single process. No production scale claims.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.phase8_factories import (  # noqa: E402
    ACTOR_MAKER, T0, active_approval, demo_approver_session,
    demo_trader_session, security_env,
)

from architecture.contracts.identifiers import new_identifier  # noqa: E402
from core.governance.gate import (  # noqa: E402
    GovernanceArtifact,
    GovernanceGate,
    GovernedArtifactType,
)
from core.security.contracts import SecurityRequest  # noqa: E402
from core.security.protection import (  # noqa: E402
    AuditChain,
    ReplayGuard,
)
from platform.audit.contracts import ActorType, AuditRecord  # noqa: E402
from platform.security.contracts import Permission  # noqa: E402

ITERATIONS = 50


def stats(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)

    def pct(p: float) -> float:
        return ordered[min(int(p * len(ordered)), len(ordered) - 1)]

    return {"p50_ms": round(pct(0.50), 3),
            "p95_ms": round(pct(0.95), 3),
            "ops_per_sec": round(1000.0 / statistics.fmean(ordered), 1),
            "n": len(ordered)}


def bench(fn):
    samples = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return stats(samples)


def at(minutes: float):
    return T0 + timedelta(minutes=minutes)


def main() -> None:
    env = security_env()
    _, trader = demo_trader_session(env)
    _, approver = demo_approver_session(env)
    approval = active_approval(env)
    gate = GovernanceGate(sessions=env["sessions"],
                          authorization=env["authz"],
                          approvals=env["approvals"],
                          audit=env["chain"])

    auth_stats = bench(lambda: env["auth"].authenticate(
        credential_id=trader.authentication.credential_id,
        secret_material="synthetic-trader-secret",
        environment="DEMO", at=at(10)))

    authz_stats = bench(lambda: env["authz"].authorize(
        session_id=trader.session_id,
        permission=Permission.DEMO_TRADE, environment="DEMO", at=at(10)))

    approval_stats = bench(lambda: env["approvals"].require_active(
        approval.approval_id, operation="POLICY_CHANGE"))

    chain = AuditChain()

    def audit_append():
        chain.append(AuditRecord(
            audit_id=new_identifier("audit_id"),
            actor_type=ActorType.USER, actor_id=ACTOR_MAKER,
            action="BENCH", entity_type="policy",
            entity_id="pol_" + "1" * 32, event_time=at(1), before=None,
            after={"i": 1}, reason="bench", source="bench",
            environment="DEMO", correlation_id="pol_" + "1" * 32))

    append_stats = bench(audit_append)
    verify_stats = bench(lambda: chain.verify())

    guard = ReplayGuard()
    requests = [SecurityRequest(
        request_id=new_identifier("request_id"),
        session_id=trader.session_id, actor_id=trader.actor_id,
        environment="DEMO", timestamp=at(10), operation="OP",
        resource="pol_" + "1" * 32) for _ in range(ITERATIONS * 4)]
    cursor = iter(requests)
    replay_stats = bench(lambda: guard.check(next(cursor), now=at(10)))

    version_counter = iter(range(1000))

    def full_privileged_path():
        artifact = GovernanceArtifact(
            artifact_type=GovernedArtifactType.POLICY,
            artifact_id="pol_" + "1" * 32, old_version="1.0.0",
            new_version=f"1.0.{next(version_counter) + 100}",
            old_content_hash="a" * 64, new_content_hash="b" * 64,
            environment="DEMO")
        gate.guard(artifact=artifact,
                   maker_session_id=approver.session_id,
                   approval_id=approval.approval_id, at=at(10),
                   reason="bench")

    # governance needs a fresh ACTIVE approval whose operation matches;
    # reuse one for measurement (evidence lookups are the hot path)
    gov_approval = active_approval(env, operation="POLICY_CHANGE")
    gate_stats = bench(lambda: gate.guard(
        artifact=GovernanceArtifact(
            artifact_type=GovernedArtifactType.POLICY,
            artifact_id="pol_" + "2" * 32, old_version="1.0.0",
            new_version=f"2.0.{next(version_counter)}",
            old_content_hash="c" * 64, new_content_hash="d" * 64,
            environment="DEMO"),
        maker_session_id=approver.session_id,
        approval_id=gov_approval.approval_id, at=at(10), reason="bench"))

    report = {
        "label": "SYNTHETIC / LOCAL / NON-PRODUCTION",
        "iterations": ITERATIONS,
        "authentication_pbkdf2": auth_stats,
        "authorization": authz_stats,
        "approval_validation": approval_stats,
        "audit_append": append_stats,
        "audit_chain_verification_full": verify_stats,
        "replay_check": replay_stats,
        "governed_transition_full_path": gate_stats,
        "notes": [
            "PBKDF2 (100k iterations) dominates authentication cost by design.",
            "In-memory stores; single process; synthetic credentials.",
            "Never interpret as production scale.",
        ],
    }
    target = Path(__file__).resolve().parents[1] / "docs" / "phase-8"
    target.mkdir(parents=True, exist_ok=True)
    (target / "phase-8-benchmark.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
