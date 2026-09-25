"""Phase 8 validator corruption tests (SECTION 42).

Every SEC rule gets a positive test (real project passes) and a
corruption test (deliberate violating fixture is caught).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from architecture.contracts.registry import find_project_root
from architecture.validator import run_architecture_validation
from architecture.validator.result import ValidationStatus

PROJECT_ROOT = find_project_root()


@pytest.fixture()
def project_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "proj"
    shutil.copytree(PROJECT_ROOT, destination,
                    ignore=shutil.ignore_patterns("__pycache__",
                                                  ".pytest_cache"))
    return destination


def _rules(result, rule_id: str) -> list:
    return [i for i in result.items if i.rule_id == rule_id]


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    """Replace EVERY occurrence: a corruption that leaves one survivor
    must still be caught."""
    target = root / rel
    src = target.read_text(encoding="utf-8")
    assert old in src, f"anchor missing in {rel}: {old!r}"
    target.write_text(src.replace(old, new), encoding="utf-8")


def _append(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.write_text(target.read_text(encoding="utf-8") + text,
                      encoding="utf-8")


class TestRealProject:
    def test_real_project_passes_all_phase8_rules(self):
        result = run_architecture_validation(PROJECT_ROOT)
        assert result.status == ValidationStatus.PASS, result.render()
        sec_failures = [i for i in result.items
                        if i.rule_id.startswith("SEC-")
                        and i.severity == ValidationStatus.FAIL]
        assert not sec_failures

    def test_all_sec_rules_registered(self):
        from architecture.validator.rules import IMPLEMENTED_RULES
        for number in range(1, 61):
            assert f"SEC-{number:03d}" in IMPLEMENTED_RULES

    def test_validator_rule_count(self):
        from architecture.validator.rules import IMPLEMENTED_RULES
        # 216 at Phase 8; Phase 9 +GUI-001..012; Phase 10 +MT5X/FDX
        # (documented extensions; boundary shifts recorded per phase)
        assert len(IMPLEMENTED_RULES) == 234


AUTH = "core/security/authentication.py"
AUTHZ = "core/security/authorization.py"
SVC = "core/security/services.py"
SEC = "core/security/secrets.py"
PROT = "core/security/protection.py"
CON = "core/security/contracts.py"
GATE = "core/governance/gate.py"
AUD = "platform/audit/contracts.py"
PLSEC = "platform/security/contracts.py"

# (rule_id, file, anchor, replacement)
CORRUPTIONS = [
    ("SEC-001", CON, "class AuthStatus", "class AuthStt"),
    ("SEC-002", AUTHZ, "session invalid", "session check rm"),
    ("SEC-003", AUTHZ, "from platform.security.contracts import",
     "from platform.security.contracts_x import"),
    ("SEC-004", CON, "SEC-SELF-APPROVAL", "SELF-APPROVAL-RULE-REMOVED"),
    ("SEC-005", SVC, "self-approval blocked", "approval check removed"),
    ("SEC-006", SVC, "is_human_checker", "checker_kind_check_removed"),
    ("SEC-007", AUTHZ, "Permission.LIVE_TRADE", "Permission.LI_RM"),
    ("SEC-008", AUTHZ, "ENVIRONMENT_MISMATCH", "ENV-CHECK-REMOVED"),
    ("SEC-009", SEC, '"<redacted>"', '"<not-redacted>"'),
    ("SEC-010", PROT, "def redact_payload", "def redct_pyload"),
    ("SEC-011", PROT, "SECRET_KEYS", "SECRET_KE_RM"),
    ("SEC-012", SEC, "revoked_at=ensure_utc(at)",
     "revoked_at=None"),
    ("SEC-013", CON, "ENCRYPTED_AT_REST_AND_TRANSIT",
     "ENCRYPTION_LEV_RM"),
    ("SEC-015", AUD, "integrity_hash", "integrity_hsh"),
    ("SEC-015", PROT, "previous_hash", "previous_hsh"),
    ("SEC-016", AUD, "correlation_id", "correlation_i"),
    ("SEC-017", AUD, "actor_id", "actor_i"),
    ("SEC-018", AUD, "event_time", "event_tim"),
    ("SEC-019", AUD, "environment", "environmen"),
    ("SEC-020", AUD, "contract_version", "contract_vers"),
    ("SEC-021", PROT, "REPLAY_DETECTED", "REPLAY_CHK_RM"),
    ("SEC-022", PROT, "IDEMPOTENT_REPEAT", "IDEMPOTNCY_RM"),
    ("SEC-023", PROT, "RateLimitVerdict.EXCEEDED",
     "RateLimitVrd.RM"),
    ("SEC-024", CON, "def validate", "def validat"),
    ("SEC-026", AUTHZ, "canonical registry", "registry mention removed"),
    ("SEC-027", AUTH, "session expired", "expiry check rm"),
    ("SEC-028", AUTH, "session revoked", "revocation check rm"),
    ("SEC-029", AUTH, "version=current.version + 1",
     "version=current.version"),
    ("SEC-030", AUTH, "AuthStatus.REVOKED", "AuthStatus.RVKD"),
    ("SEC-031", AUTHZ, '"EMERGENCY_RELEASE"', '"EMERGEN_RM"'),
    ("SEC-032", GATE, "new_version", "new_versn"),
    ("SEC-033", GATE, "Permission.MODIFY_RISK", "Permission.MOD_RSK_RM"),
    ("SEC-034", GATE, "Permission.MODIFY_POLICY",
     "Permission.MOD_POL_RM"),
    ("SEC-035", GATE, "Permission.APPROVE", "Permission.APPRV"),
    ("SEC-036", GATE, "GovernedArtifactType.STRATEGY",
     "GovernedArtifactType.STRAT"),
    ("SEC-037", CON, "NON_HUMAN_CHECKERS", "HUMAN_CHECKE_RM"),
    ("SEC-038", SVC, "AI cannot satisfy a human approval",
     "checker kind note removed"),
    ("SEC-042", CON, "manifest_hash", "manifest_hsh"),
    ("SEC-043", PROT, "never restored", "restore note removed"),
    ("SEC-044", CON, "incidents preserve evidence",
     "incident evidence note removed"),
    ("SEC-045", PROT, "SECURITY_EVENT_TYPES", "EVENT_TYP_RM"),
    ("SEC-046", AUTHZ, "no cross-environment fallback",
     "cross environment fallback allowed"),
    ("SEC-047", CON, "never from the client body",
     "client body note removed"),
    ("SEC-048", GATE, "approval maker does not match",
     "approval maker note removed"),
    ("SEC-049", PLSEC, "def check_grant", "def check_grnt"),
    ("SEC-050", AUTHZ, "ENVIRONMENT_PERMISSION_MISMATCH",
     "ENV-PERM-CHECK-REMOVED"),
    ("SEC-051", SVC, "approval request expired",
     "stale approval check removed"),
    ("SEC-052", AUD, "model_version", "model_versn"),
    ("SEC-053", CON, "content_hash mismatch", "hash mismatch removed"),
    ("SEC-054", GATE, "maker and checker must differ",
     "separation note removed"),
    ("SEC-055", GATE, "GOVERNANCE_TRANSITION", "TRANSITI_RM"),
    ("SEC-056", GATE, "require_verified", "require_verifd"),
    ("SEC-056", PROT, "privileged operations are ",
     "privileged ops note removed "),
    ("SEC-057", AUTHZ, "APPROVAL_REQUIRED_OPERATIONS",
     "APPROVAL_OPERATIO_RM"),
    ("SEC-058", AUTH, "pbkdf2_hmac", "kdf_removed"),
    ("SEC-059", AUTHZ, "PERMISSION_DENIED", "PERMISSION-DENIED-REMOVED"),
    ("SEC-060", PLSEC, "allowed_environments", "allowed_envs_removed"),
]


class TestCorruption:
    @pytest.mark.parametrize("rule_id,rel,old,new", CORRUPTIONS,
                             ids=[c[0] for c in CORRUPTIONS])
    def test_corruption_detected(self, project_copy, rule_id, rel, old, new):
        _edit(project_copy, rel, old, new)
        result = run_architecture_validation(project_copy)
        assert _rules(result, rule_id), \
            f"{rule_id} did not fire for corruption of {rel}:{old!r}"

    def test_sec014_mutable_audit_record(self, project_copy):
        src = (project_copy / AUD).read_text(encoding="utf-8")
        marker = "@dataclass(frozen=True)\nclass AuditRecord:"
        assert marker in src
        _edit(project_copy, AUD, marker,
              "@dataclass\nclass AuditRecord:")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SEC-014")

    def test_sec025_bypass_marker(self, project_copy):
        _append(project_copy, AUTHZ,
                "\nDEFAULT_POLICY = \"ALLOW_ALL\"\n")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SEC-025")

    def test_sec039_broker_shortcut(self, project_copy):
        _append(project_copy, AUTHZ,
                "\nfrom adapters.mt5.execution import MT5ExecutionAdapter\n")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SEC-039")

    def test_sec040_second_authorization_engine(self, project_copy):
        _append(project_copy, AUTHZ,
                "\nclass RolePermissionRegistry:\n    pass\n")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SEC-040")

    def test_sec041_second_audit_authority(self, project_copy):
        _append(project_copy, PROT,
                "\nclass SecurityAuditRepository:\n    pass\n")
        result = run_architecture_validation(project_copy)
        assert _rules(result, "SEC-041")


class TestPhaseBoundary:
    def test_boundx_phase10_web_blocked(self, project_copy):
        """Phase 9 made the desktop current-phase scope: the boundary
        moved to Phase 10 (web workspace / mobile)."""
        app = project_copy / "ui" / "web" / "app.py"
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text("def main():\n    return 1\n", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")

    def test_boundx_web_workspace_blocked(self, project_copy):
        app = project_copy / "ui" / "web" / "workspace.py"
        app.write_text("def main():\n    return 1\n", encoding="utf-8")
        assert _rules(run_architecture_validation(project_copy),
                      "BOUNDX-001")

    def test_governance_module_now_legitimate(self, project_copy):
        """core/governance is Phase 8 scope: creating it must NOT trip
        BOUNDX (it exists in the real project and validates)."""
        result = run_architecture_validation(project_copy)
        assert not [i for i in result.items
                    if i.rule_id == "BOUNDX-001"
                    and "governance" in i.location]
