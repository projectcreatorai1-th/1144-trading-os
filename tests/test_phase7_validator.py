"""Phase 7 validator corruption tests (SECTION 129).

EVERY AI-001..AI-036 rule gets a positive test (real project passes) and
a negative/corruption test (deliberate violating fixture is caught).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from architecture.contracts.registry import find_project_root
from architecture.validator import run_architecture_validation
from architecture.validator.result import ValidationStatus

PROJECT_ROOT = find_project_root()

INTEL = "core/intelligence"


@pytest.fixture()
def project_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "proj"
    shutil.copytree(PROJECT_ROOT, destination)
    return destination


def _rules(result, rule_id: str) -> list:
    return [i for i in result.items if i.rule_id == rule_id]


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.write_text(text, encoding="utf-8")


def _append(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.write_text(target.read_text(encoding="utf-8") + text,
                      encoding="utf-8")


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    """Replace EVERY occurrence: a corruption that leaves one survivor
    must still be caught (or the rule is too weak to matter)."""
    target = root / rel
    src = target.read_text(encoding="utf-8")
    assert old in src, f"fixture anchor missing in {rel}: {old!r}"
    target.write_text(src.replace(old, new), encoding="utf-8")


class TestRealProject:
    def test_real_project_passes_all_phase7_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()
        ai_failures = [i for i in result.items
                       if i.rule_id.startswith("AI-")
                       and i.severity == ValidationStatus.FAIL]
        assert not ai_failures

    def test_phase7_rules_are_registered(self):
        from architecture.validator.rules import IMPLEMENTED_RULES
        for number in range(1, 37):
            assert f"AI-{number:03d}" in IMPLEMENTED_RULES


class TestAuthorityBoundaryCorruption:
    def test_ai001_oms_import(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nfrom core.oms.engine import OrderManagementSystem\n")
        assert _rules(run_architecture_validation(project_copy), "AI-001")

    def test_ai002_ems_import(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nfrom core.ems.engine import ExecutionManagementSystem\n")
        assert _rules(run_architecture_validation(project_copy), "AI-002")

    def test_ai003_broker_adapter_import(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nfrom adapters.mt5.execution import MT5ExecutionAdapter\n")
        assert _rules(run_architecture_validation(project_copy), "AI-003")

    def test_ai004_risk_decision_construction(self, project_copy):
        _append(project_copy, f"{INTEL}/proposal.py",
                "\nfrom core.risk.contracts import RiskDecision\n"
                "def make(x):\n    return RiskDecision\n")
        assert _rules(run_architecture_validation(project_copy), "AI-004")

    def test_ai005_policy_import(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nfrom core.policy.contracts import Policy\n")
        assert _rules(run_architecture_validation(project_copy), "AI-005")

    def test_ai006_portfolio_import(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nfrom core.portfolio.contracts import Position\n")
        assert _rules(run_architecture_validation(project_copy), "AI-006")

    def test_ai007_strategy_import(self, project_copy):
        _append(project_copy, f"{INTEL}/proposal.py",
                "\nfrom core.strategy.contracts import Strategy\n")
        assert _rules(run_architecture_validation(project_copy), "AI-007")

    def test_ai008_risk_limit_mutation(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\ndef relax(x):\n    return x + hard_limits_marker\n")
        assert _rules(run_architecture_validation(project_copy), "AI-008")

    def test_ai009_lifecycle_missing_approval(self, project_copy):
        # strip every approval token (checks, context keys, messages)
        _edit(project_copy, f"{INTEL}/lifecycle.py",
              "human_approval", "approval_gone")
        _edit(project_copy, f"{INTEL}/lifecycle.py",
              "HUMAN_APPROVAL", "APPROVAL_GONE")
        assert _rules(run_architecture_validation(project_copy), "AI-009")

    def test_ai009_lifecycle_missing_permission_check(self, project_copy):
        # every permission check goes (promotion + suspend + retire)
        _edit(project_copy, f"{INTEL}/lifecycle.py",
              "actor.require", "actor.skip")
        assert _rules(run_architecture_validation(project_copy), "AI-009")

    def test_ai010_self_deploy(self, project_copy):
        _append(project_copy, f"{INTEL}/lifecycle.py",
                "\ndef deploy_self(model):\n    return model\n")
        assert _rules(run_architecture_validation(project_copy), "AI-010")

    def test_ai011_live_inference_environment(self, project_copy):
        _edit(project_copy, f"{INTEL}/training.py",
              'inference_environments=("RESEARCH",)',
              'inference_environments=("RESEARCH", "LIVE")')
        assert _rules(run_architecture_validation(project_copy), "AI-011")


class TestDataDisciplineCorruption:
    def test_ai012_pit_oracle_removed(self, project_copy):
        # the PIT oracle token must vanish entirely from the engine
        _edit(project_copy, f"{INTEL}/feature.py",
              "visible_at", "visible_gone")
        assert _rules(run_architecture_validation(project_copy), "AI-012")

    def test_ai013_provenance_removed(self, project_copy):
        # every provenance mention goes (field + docs + builders)
        _edit(project_copy, f"{INTEL}/inference.py",
              "provenance", "provx")
        assert _rules(run_architecture_validation(project_copy),
                      "AI-013")

    def test_ai014_semver_check_removed(self, project_copy):
        # remove BOTH ModelDefinition and InferenceRequest checks
        _edit(project_copy, f"{INTEL}/contracts.py",
              "SemVer.parse(self.model_version",
              "pass  # version check removed; SemVer disabled(")
        assert _rules(run_architecture_validation(project_copy), "AI-014")

    def test_ai015_artifact_hash_removed_from_contract(self, project_copy):
        # every artifact_hash mention must go (declarations + checks)
        # replacement must not contain the scanned token
        _edit(project_copy, f"{INTEL}/contracts.py",
              "artifact_hash", "artifact_hsh")
        assert _rules(run_architecture_validation(project_copy), "AI-015")

    def test_ai016_feature_schema_hash_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/contracts.py",
              "feature_schema_hash", "schema_hash_gone")
        assert _rules(run_architecture_validation(project_copy), "AI-016")

    def test_ai017_dataset_content_hash_optional(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / \
            "intelligence_dataset.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["fields"]["content_hash"]["required"] = False
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "AI-017")

    def test_ai017_dataset_immutable_constraint_removed(self, project_copy):
        schema_path = project_copy / "architecture" / "schemas" / \
            "intelligence_dataset.yaml"
        schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
        schema["constraints"] = [c for c in schema["constraints"]
                                 if "immutable" not in c]
        schema_path.write_text(yaml.safe_dump(schema), encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy), "AI-017")

    def test_ai018_environment_gate_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/inference.py",
              '"ENVIRONMENT_MISMATCH: model approved for "',
              '"env-gate-removed "')
        assert _rules(run_architecture_validation(project_copy), "AI-018")

    def test_ai019_block_semantics_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/inference.py",
              "SafetyDecision.BLOCK", "SafetyDecision.Neutralized")
        assert _rules(run_architecture_validation(project_copy), "AI-019")

    def test_ai019_pit_enforcement_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/inference.py",
              '"POINT_IN_TIME_VIOLATION: snapshot contains data not "',
              '"pit-check-removed "')
        assert _rules(run_architecture_validation(project_copy), "AI-019")


class TestSecondEngineCorruption:
    def test_ai020_second_risk_engine(self, project_copy):
        _append(project_copy, f"{INTEL}/inference.py",
                "\nclass RiskEngine:\n    pass\n")
        assert _rules(run_architecture_validation(project_copy), "AI-020")

    def test_ai021_second_state_engine(self, project_copy):
        _append(project_copy, f"{INTEL}/lifecycle.py",
                "\nclass ModelLifecycleStateEngine:\n    pass\n")
        assert _rules(run_architecture_validation(project_copy), "AI-021")

    def test_ai022_second_event_store(self, project_copy):
        _append(project_copy, f"{INTEL}/events.py",
                "\nclass IntelligenceEventStore:\n    pass\n")
        assert _rules(run_architecture_validation(project_copy), "AI-022")

    def test_ai023_second_ledger(self, project_copy):
        _append(project_copy, f"{INTEL}/contracts.py",
                "\nclass IntelligenceLedger:\n    pass\n")
        assert _rules(run_architecture_validation(project_copy), "AI-023")

    def test_ai024_second_replay_engine(self, project_copy):
        _append(project_copy, f"{INTEL}/replay.py",
                "\nclass IntelligenceReplayEngine:\n    pass\n")
        assert _rules(run_architecture_validation(project_copy), "AI-024")


class TestStrategyBoundaryCorruption:
    def test_ai025_strategy_lifecycle_mutation(self, project_copy):
        _append(project_copy, f"{INTEL}/proposal.py",
                "\nfrom core.strategy.contracts import StrategyLifecycle\n")
        assert _rules(run_architecture_validation(project_copy), "AI-025")

    def test_ai026_latest_model(self, project_copy):
        _append(project_copy, f"{INTEL}/inference.py",
                "\ndef latest_model(registry):\n    return None\n")
        assert _rules(run_architecture_validation(project_copy), "AI-026")

    def test_ai027_latest_dataset(self, project_copy):
        _append(project_copy, f"{INTEL}/dataset.py",
                "\ndef latest_dataset(store):\n    return None\n")
        assert _rules(run_architecture_validation(project_copy), "AI-027")

    def test_ai028_fallback_model(self, project_copy):
        _append(project_copy, f"{INTEL}/inference.py",
                "\nfallback_model = None\n")
        assert _rules(run_architecture_validation(project_copy), "AI-028")

    def test_ai029_feature_mutation_allowed(self, project_copy):
        _edit(project_copy, f"{INTEL}/registry.py",
              "already registered with different",
              "already registered; overwriting")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "AI-029")

    def test_ai030_confidence_field_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/contracts.py",
              "confidence: float | None",
              "confidence_removed: float | None")
        assert _rules(run_architecture_validation(project_copy), "AI-030")

    def test_ai031_causal_disclaimer_removed(self, project_copy):
        # strip every causal mention (declaration AND validation)
        _edit(project_copy, f"{INTEL}/outputs.py", "causal", "causl")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "AI-031")

    def test_ai032_artifact_integrity_removed(self, project_copy):
        _edit(project_copy, f"{INTEL}/adapter.py",
              '"artifact hash mismatch (corrupted or forged artifact rejected)"',
              '"integrity check removed"')
        assert _rules(run_architecture_validation(project_copy), "AI-032")

    def test_ai033_mutable_model_definition(self, project_copy):
        src = (project_copy / f"{INTEL}/contracts.py").read_text("utf-8")
        marker = "@dataclass(frozen=True)\nclass ModelDefinition:"
        assert marker in src
        _edit(project_copy, f"{INTEL}/contracts.py", marker,
              "@dataclass\nclass ModelDefinition:")
        assert _rules(run_architecture_validation(project_copy), "AI-033")

    def test_ai034_production_state_write(self, project_copy):
        _append(project_copy, f"{INTEL}/training.py",
                "\nfrom core.oms.engine import OrderManagementSystem\n")
        assert _rules(run_architecture_validation(project_copy), "AI-034")

    def test_ai035_replay_environment_removed(self, project_copy):
        src = (project_copy / f"{INTEL}/replay.py").read_text("utf-8")
        _write(project_copy, f"{INTEL}/replay.py",
               src.replace('REPLAY', 'RESEARCHX'))
        assert _rules(run_architecture_validation(project_copy), "AI-035")

    def test_ai036_order_construction(self, project_copy):
        _append(project_copy, f"{INTEL}/proposal.py",
                "\nfrom core.execution.contracts import Order\n")
        assert _rules(run_architecture_validation(project_copy), "AI-036")


class TestBoundaryRule:
    def test_boundx_phase8_governance_now_legitimate(self, project_copy):
        """Phases 8/9 made governance + desktop current-phase scope;
        the boundary is now Phase 10 web/mobile."""
        gov = project_copy / "core" / "governance"
        gov.mkdir(exist_ok=True)
        (gov / "__init__.py").write_text("", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert not [i for i in result.items
                    if i.rule_id == "BOUNDX-001"
                    and "governance" in i.location]
        app = project_copy / "ui" / "desktop" / "app.py"
        app.write_text("placeholder", encoding="utf-8")
        result = run_architecture_validation(project_copy)
        assert not [i for i in result.items
                    if i.rule_id == "BOUNDX-001"
                    and "desktop" in i.location]
        web = project_copy / "ui" / "web" / "app.py"
        web.parent.mkdir(parents=True, exist_ok=True)
        web.write_text("placeholder", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")

    def test_boundx_phase10_web_blocked(self, project_copy):
        app = project_copy / "ui" / "web" / "app.py"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text("placeholder", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")
