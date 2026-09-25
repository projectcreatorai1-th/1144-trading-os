"""Manifest <-> actual project structure consistency tests (SECTION 31)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from architecture.contracts.registry import find_project_root

ROOT = find_project_root()
ARCH = yaml.safe_load((ROOT / "architecture" / "architecture.yaml").read_text(encoding="utf-8"))
MANIFEST = yaml.safe_load((ROOT / "architecture" / "manifest.yaml").read_text(encoding="utf-8"))

REQUIRED_DOCS = [
    # Phase 0
    "architecture.md",
    "contracts.md",
    "versioning.md",
    "state-machines.md",
    "dependency-rules.md",
    "environment-model.md",
    "audit-model.md",
    "decision-trace.md",
    "phase-0-report.md",
    # Phase 1
    "phase-1-data.md",
    "phase-1-time.md",
    "phase-1-events.md",
    "data-quality.md",
    "data-lineage.md",
    "event-store.md",
    "event-replay.md",
    "phase-1-report.md",
    "phase-1-benchmark.md",
    # Phase 2
    "phase-2-state.md",
    "phase-2-ledger.md",
    "phase-2-reconciliation.md",
    "state-rebuild.md",
    "ledger-integrity.md",
    "reconciliation-model.md",
    "phase-2-recovery.md",
    "phase-2-report.md",
    "phase-2-benchmark.md",
    # Phase 3
    "phase-3-policy.md",
    "phase-3-risk.md",
    "policy-lifecycle.md",
    "policy-evaluation.md",
    "risk-context.md",
    "risk-rules.md",
    "risk-state-machine.md",
    "risk-decision.md",
    "fail-closed-matrix.md",
    "risk-replay.md",
    "phase-3-recovery.md",
    "phase-3-report.md",
    "phase-3-benchmark.md",
    # Phase 4
    "phase-4-strategy.md",
    "strategy-contract.md",
    "strategy-lifecycle.md",
    "strategy-capability.md",
    "strategy-intent.md",
    "strategy-eligibility.md",
    "strategy-kill-criteria.md",
    "strategy-health.md",
    "strategy-dependency-graph.md",
    "phase-4-portfolio.md",
    "portfolio-contract.md",
    "portfolio-lifecycle.md",
    "portfolio-membership.md",
    "capital-allocation.md",
    "risk-budget-allocation.md",
    "portfolio-exposure.md",
    "capacity-model.md",
    "liquidity-budget.md",
    "strategy-portfolio-risk-flow.md",
    "phase-4-replay.md",
    "phase-4-recovery.md",
    "phase-4-report.md",
    "phase-4-benchmark.md",
    # Phase 5
    "phase-5/phase-5-architecture.md",
    "phase-5/order-contract.md",
    "phase-5/oms.md",
    "phase-5/ems.md",
    "phase-5/execution-adapter.md",
    "phase-5/mt5-adapter.md",
    "phase-5/execution-state-machine.md",
    "phase-5/execution-failure-matrix.md",
    "phase-5/execution-recovery.md",
    "phase-5/environment-isolation.md",
    "phase-5/execution-security.md",
    "phase-5/reconciliation.md",
    "phase-5/phase-5-test-report.md",
    "phase-5/phase-5-report.md",
    "phase-5/phase-5-benchmark.md",
    # Phase 6
    "phase-6/phase-6-architecture.md",
    "phase-6/dataset-contract.md",
    "phase-6/point-in-time.md",
    "phase-6/research-engine.md",
    "phase-6/backtest-engine.md",
    "phase-6/execution-model.md",
    "phase-6/replay.md",
    "phase-6/bias-audit.md",
    "phase-6/validation.md",
    "phase-6/walk-forward.md",
    "phase-6/stress-testing.md",
    "phase-6/robustness.md",
    "phase-6/research-artifacts.md",
    "phase-6/reproducibility.md",
    "phase-6/recovery.md",
    "phase-6/phase-6-report.md",
    "phase-6/phase-6-benchmark.md",
    # Phase 7
    "phase-7/phase-7-report.md",
    "phase-7/phase-7-architecture.md",
    "phase-7/phase-7-contracts.md",
    "phase-7/phase-7-security.md",
    "phase-7/phase-7-ai-boundary.md",
    "phase-7/phase-7-model-lifecycle.md",
    "phase-7/phase-7-feature-lineage.md",
    "phase-7/phase-7-replay.md",
    "phase-7/phase-7-benchmark.md",
    "phase-7/phase-7-failure-tests.md",
    "phase-7/phase-7-e2e.md",
    "phase-7/phase-7-invariants.md",
    "phase-7/phase-7-validator.md",
    "phase-7/phase-7-gap-audit.md",
    # Phase 9
    "phase-9/README.md",
    "phase-9/ARCHITECTURE.md",
    "phase-9/WORKSTATION.md",
    "phase-9/SECURITY.md",
    "phase-9/TEST_REPORT.md",
    "phase-9/FINAL_REPORT.md",
]


class TestModuleStructure:
    @pytest.mark.parametrize("module_id", sorted(m["id"] for m in ARCH["modules"]))
    def test_module_has_location_on_disk(self, module_id: str):
        if module_id.startswith(("core.", "platform.", "adapters.", "ui.")):
            area, _, name = module_id.partition(".")
            assert (ROOT / area / name).is_dir(), f"{module_id} has no package directory"
        elif module_id.startswith("architecture."):
            _, _, name = module_id.partition(".")
            assert (ROOT / "architecture" / name).is_dir(), f"{module_id} has no package directory"
        else:
            assert (ROOT / module_id).is_dir(), f"{module_id} has no directory"

    def test_manifest_lists_all_modules(self):
        arch_ids = {m["id"] for m in ARCH["modules"]}
        manifest_ids = set(MANIFEST["modules"])
        assert arch_ids == manifest_ids

    def test_domains_declared(self):
        for domain in MANIFEST["domains"]:
            assert domain in {m["domain"] for m in ARCH["modules"]}


class TestRegistryFiles:
    @pytest.mark.parametrize("registry", ARCH["registries"])
    def test_registry_file_exists(self, registry):
        assert (ROOT / registry["file"]).is_file()

    @pytest.mark.parametrize("relpath", MANIFEST["source_of_truth_files"])
    def test_manifest_source_of_truth_exists(self, relpath):
        assert (ROOT / relpath).is_file()


class TestVersionConsistency:
    def test_manifest_system_versions_match_architecture(self):
        for key in ("system_version", "architecture_version", "contract_version", "schema_version"):
            assert MANIFEST["system"][key] == ARCH["system"][key]

    def test_registries_declared_with_versions(self):
        for registry in ARCH["registries"]:
            assert registry["version"].count(".") == 2


class TestDocumentation:
    @pytest.mark.parametrize("doc", REQUIRED_DOCS)
    def test_required_document_exists(self, doc):
        path = ROOT / "docs" / doc
        assert path.is_file(), f"missing documentation: docs/{doc}"
        assert path.stat().st_size > 0
