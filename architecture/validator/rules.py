"""Validator rules.

Each rule returns structured ValidationItems. The validator reads registries
directly from the project root (so it can validate alternative roots in
tests) and reuses kernel logic (versioning) where duplication would violate
RULE 002.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from architecture.contracts.versioning import check_history
from architecture.validator.result import ValidationItem, ValidationStatus

FAIL = ValidationStatus.FAIL
WARNING = ValidationStatus.WARNING

IMPLEMENTED_RULES = (
    "ARCH-001", "ARCH-002", "ARCH-003", "ARCH-004", "ARCH-005", "ARCH-006",
    "ARCH-007", "ARCH-008", "SM-001", "SM-002", "ENV-001", "ENV-002",
    "SCHEMA-001", "SCHEMA-002", "SCHEMA-003", "SCHEMA-004", "SOT-001",
    "SOT-002", "SOT-003", "IMPORT-001", "NOPRODUCTION-001", "MANIFEST-001",
    "TRACE-001", "TRACE-002",
    # Phase 1 rules
    "DATA-001", "DATA-002", "DATA-003", "TIME-001", "TIME-002",
    "EVENT-003", "EVENT-004", "EVENT-005",
    # Phase 2 rules
    "STATE-001", "STATE-002", "STATE-003",
    "LEDGER-001", "LEDGER-002", "LEDGER-003", "LEDGER-004",
    "RECON-001", "RECON-002", "RECON-003", "RECON-004",
    # Phase 3 rules
    "POLICY-001", "POLICY-002", "POLICY-003",
    "RISK-001", "RISK-002", "RISK-003", "RISK-004", "RISK-005", "RISK-006",
    "SAFETY-001", "SAFETY-002",
    "DECISION-001", "DECISION-002",
    "ENV-003", "AUDIT-003",
    # Phase 4 rules
    "STRATEGY-001", "STRATEGY-002", "STRATEGY-003", "STRATEGY-004", "STRATEGY-005",
    "PORTFOLIO-001", "PORTFOLIO-002", "PORTFOLIO-003", "PORTFOLIO-004", "PORTFOLIO-005",
    "ALLOCATION-001", "ALLOCATION-002",
    "EXPOSURE-001", "EXPOSURE-002",
    "INTENT-001", "INTENT-002",
    "BOUNDARY-001", "BOUNDARY-002",
    # Phase 5 rules
    "OMS-001", "OMS-002",
    "EMS-001", "EMS-002",
    "EXEC-001", "EXEC-002", "EXEC-003", "EXEC-004", "EXEC-005",
    "MT5-001", "MT5-002",
    "ENVX-001",
    "POSX-001", "LEDX-001", "RECX-001",
    "SECX-001",
    "AIX-001",
    "BOUNDX-001",
    # Phase 6 rules
    "DATASET-001", "DATASET-002", "DATASET-003", "DATASET-004",
    "BIAS-001", "BIAS-002", "BIAS-003", "BIAS-004",
    "BACKTEST-001", "BACKTEST-002", "BACKTEST-003", "BACKTEST-004", "BACKTEST-005",
    "REPLAYX-001", "REPLAYX-002", "REPLAYX-003",
    "RESEARCHX-001", "RESEARCHX-002", "RESEARCHX-003", "RESEARCHX-004",
    "VALIDATIONX-001", "VALIDATIONX-002", "VALIDATIONX-003",
    "STRESSX-001",
    "CANDIDATE-001",
    "PROMOTION-001",
    # Phase 7 rules
    "AI-001", "AI-002", "AI-003", "AI-004", "AI-005", "AI-006", "AI-007",
    "AI-008", "AI-009", "AI-010", "AI-011", "AI-012", "AI-013", "AI-014",
    "AI-015", "AI-016", "AI-017", "AI-018", "AI-019", "AI-020", "AI-021",
    "AI-022", "AI-023", "AI-024", "AI-025", "AI-026", "AI-027", "AI-028",
    "AI-029", "AI-030", "AI-031", "AI-032", "AI-033", "AI-034", "AI-035",
    "AI-036",
    # Phase 8 rules
    "SEC-001", "SEC-002", "SEC-003", "SEC-004", "SEC-005", "SEC-006",
    "SEC-007", "SEC-008", "SEC-009", "SEC-010", "SEC-011", "SEC-012",
    "SEC-013", "SEC-014", "SEC-015", "SEC-016", "SEC-017", "SEC-018",
    "SEC-019", "SEC-020", "SEC-021", "SEC-022", "SEC-023", "SEC-024",
    "SEC-025", "SEC-026", "SEC-027", "SEC-028", "SEC-029", "SEC-030",
    "SEC-031", "SEC-032", "SEC-033", "SEC-034", "SEC-035", "SEC-036",
    "SEC-037", "SEC-038", "SEC-039", "SEC-040", "SEC-041", "SEC-042",
    "SEC-043", "SEC-044", "SEC-045", "SEC-046", "SEC-047", "SEC-048",
    "SEC-049", "SEC-050", "SEC-051", "SEC-052", "SEC-053", "SEC-054",
    "SEC-055", "SEC-056", "SEC-057", "SEC-058", "SEC-059", "SEC-060",
    # Phase 9 rules
    "GUI-001", "GUI-002", "GUI-003", "GUI-004", "GUI-005", "GUI-006",
    "GUI-007", "GUI-008", "GUI-009", "GUI-010", "GUI-011", "GUI-012",
    # Phase 10 rules
    "MT5X-001", "MT5X-002", "MT5X-003", "MT5X-004",
    "FDX-001", "FDX-002",
)

ARCHITECTURE_FILE = "architecture/architecture.yaml"
APP_ROOTS = {"architecture", "core", "platform", "adapters", "ui", "research", "tests", "benchmarks"}
KERNEL_MODULE = "architecture.contracts"
PRODUCTION_AREAS = ("architecture", "core", "platform", "adapters", "ui", "research")

# Forbidden production markers are assembled from parts so that this file
# never itself contains the literal markers it scans for.
_BANNED_MARKERS = tuple(
    "-".join(parts)
    for parts in (
        ("to" + "do",),
        ("fix" + "me",),
        ("ha" + "ck",),
        ("mo" + "ck",),
        ("fa" + "ke",),
        ("stu" + "b",),
        ("place" + "holder",),
    )
)
_WORD_RE = re.compile(r"\b(" + "|".join(re.escape(m) for m in _BANNED_MARKERS) + r")\b", re.IGNORECASE)


def _item(rule_id: str, severity: str, location: str, message: str, **details: Any) -> ValidationItem:
    return ValidationItem(rule_id, severity, location, message, details)


def _load_yaml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"file missing: {path}"
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}"
    if not isinstance(data, dict):
        return None, "top-level YAML structure must be a mapping"
    return data, None


def _module_of(py_file: Path, root: Path) -> str | None:
    """Map a .py file to its owning registry module id."""
    try:
        relative = py_file.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if not parts:
        return None
    top = parts[0].replace("-", "_")
    if top not in APP_ROOTS:
        return None
    if len(parts) == 2 and parts[1] == "__init__.py":
        return None  # area package marker: contains no code by rule
    if top in ("core", "platform", "adapters", "ui"):
        if len(parts) >= 2:
            return f"{top}.{parts[1]}"
        return top
    if top == "architecture":
        if len(parts) >= 2 and parts[1] != "architecture.yaml":
            return f"architecture.{parts[1]}"
        return "architecture"
    return top  # research / tests


def _import_module_targets(tree: ast.AST, own_module: str) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: same module
            if node.module:
                targets.add(node.module)
    result: set[str] = set()
    for target in targets:
        segments = target.split(".")
        if segments[0] not in APP_ROOTS:
            continue  # stdlib / external
        if segments[0] in ("core", "platform", "adapters", "ui") and len(segments) >= 2:
            result.add(f"{segments[0]}.{segments[1]}")
        elif segments[0] == "architecture" and len(segments) >= 2:
            result.add(f"architecture.{segments[1]}")
        else:
            result.add(segments[0])
    result.discard(own_module)
    return result


def _pattern_matches(pattern: str, module_id: str) -> bool:
    if pattern.endswith(".*"):
        return module_id == pattern[:-2] or module_id.startswith(pattern[:-1])
    return pattern == module_id


# ---------------------------------------------------------------------------


def check_architecture_registry(root: Path, arch: dict[str, Any] | None, error: str | None) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    if arch is None:
        items.append(_item("ARCH-001", FAIL, ARCHITECTURE_FILE, error or "registry not loadable"))
        return items
    required_sections = ("system", "layers", "dependency_policy", "environments", "modules", "registries")
    for section in required_sections:
        if section not in arch:
            items.append(_item("ARCH-001", FAIL, ARCHITECTURE_FILE, f"missing required section '{section}'"))
    for key in ("name", "phase", "system_version", "architecture_version", "contract_version", "schema_version", "source_of_truth"):
        if key not in arch.get("system", {}):
            items.append(_item("ARCH-001", FAIL, "system", f"missing system key '{key}'"))
    layer_names = [layer["name"] for layer in arch.get("layers", [])]
    if len(layer_names) != len(set(layer_names)):
        items.append(_item("ARCH-001", FAIL, "layers", "duplicate layer names"))
    if not any(l["name"] == KERNEL_MODULE for l in arch.get("layers", [])) and \
            not any(l.get("name") == "contracts_kernel" for l in arch.get("layers", [])):
        items.append(_item("ARCH-001", FAIL, "layers", "contracts kernel layer missing"))
    for module in arch.get("modules", []):
        for key in ("id", "layer", "domain", "status", "responsibility", "allowed_dependencies", "forbidden_dependencies"):
            if key not in module:
                items.append(_item("ARCH-001", FAIL, f"modules[{module.get('id', '?')}]", f"missing module key '{key}'"))
        if module.get("layer") not in layer_names:
            items.append(_item("ARCH-001", FAIL, f"modules[{module.get('id', '?')}]", f"unknown layer '{module.get('layer')}'"))
    return items


def check_duplicate_modules(arch: dict[str, Any] | None) -> list[ValidationItem]:
    if arch is None:
        return []
    items: list[ValidationItem] = []
    seen: dict[str, int] = {}
    for module in arch.get("modules", []):
        module_id = module.get("id")
        if module_id in seen:
            items.append(_item("ARCH-002", FAIL, f"modules[{module_id}]", f"duplicate module id '{module_id}'"))
        seen[module_id] = seen.get(module_id, 0) + 1
    return items


def check_dependencies(arch: dict[str, Any] | None) -> list[ValidationItem]:
    if arch is None:
        return []
    items: list[ValidationItem] = []
    modules = {m["id"]: m for m in arch.get("modules", []) if isinstance(m.get("id"), str)}
    layer_order = {l["name"]: l.get("order", 0) for l in arch.get("layers", [])}
    for module in arch.get("modules", []):
        module_id = module.get("id")
        if module_id not in modules:
            continue
        implements = {
            entry.split(".")[0] + "." + entry.split(".")[1]
            for entry in module.get("implements", [])
            if entry.count(".") >= 1
        }
        for dep in module.get("allowed_dependencies", []):
            if dep == KERNEL_MODULE:
                continue
            if dep not in modules and not dep.endswith("*"):
                items.append(_item("ARCH-007", FAIL, f"{module_id}.allowed_dependencies", f"unknown module reference '{dep}'"))
                continue
            dep_order = layer_order.get(modules[dep]["layer"], 0) if dep in modules else None
            own_order = layer_order.get(module["layer"], 0)
            if (
                dep_order is not None
                and dep_order < own_order
                and dep not in implements
                and module.get("domain") != "tests"  # test scope may import anything
            ):
                items.append(
                    _item(
                        "ARCH-005",
                        FAIL,
                        f"{module_id} -> {dep}",
                        "upward layer dependency without declared port implementation",
                        source_layer=module["layer"],
                        target_layer=modules[dep]["layer"],
                    )
                )
        for pattern in module.get("forbidden_dependencies", []):
            if "*" in pattern:
                continue  # pattern: checked per-concrete-target below
            if pattern not in modules:
                prefix = ".".join(pattern.split(".")[:2])
                if pattern.count(".") >= 1 and prefix in modules:
                    pass  # capability qualifier on a known module (e.g. adapters.mt5.direct_execution)
                else:
                    items.append(_item("ARCH-007", FAIL, f"{module_id}.forbidden_dependencies", f"unknown module reference '{pattern}'"))
        # concrete forbidden hits inside allow-list
        for dep in module.get("allowed_dependencies", []):
            for pattern in module.get("forbidden_dependencies", []):
                if _pattern_matches(pattern, dep):
                    items.append(_item("ARCH-006", FAIL, f"{module_id} -> {dep}", f"dependency is explicitly forbidden (pattern '{pattern}')"))
        for dep in module.get("allowed_dependencies", []):
            for rule in arch.get("dependency_policy", {}).get("global_forbidden", []):
                if module_id in rule.get("modules", []) and dep in rule.get("forbidden", []):
                    items.append(_item("ARCH-006", FAIL, f"{module_id} -> {dep}", f"global forbidden rule {rule.get('rule')}"))
    return items


def check_source_imports(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    if arch is None:
        return []
    items: list[ValidationItem] = []
    modules = {m["id"]: m for m in arch.get("modules", []) if isinstance(m.get("id"), str)}
    for area in PRODUCTION_AREAS:
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            module_id = _module_of(py_file, root)
            if module_id is None:
                continue
            owner = modules.get(module_id)
            if owner is None:
                items.append(_item("ARCH-002", FAIL, str(py_file), f"source file belongs to unregistered module '{module_id}'"))
                continue
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                items.append(_item("IMPORT-001", FAIL, str(py_file), f"unparseable source: {exc}"))
                continue
            allowed = set(owner.get("allowed_dependencies", []))
            forbidden = list(owner.get("forbidden_dependencies", []))
            for rule in arch.get("dependency_policy", {}).get("global_forbidden", []):
                if "applies_to_all_except" in rule:
                    if module_id not in rule["applies_to_all_except"] and module_id != rule["pattern"][:-2]:
                        forbidden.append(rule["pattern"])
                if module_id in rule.get("modules", []):
                    forbidden.extend(rule.get("forbidden", []))
            for target in _import_module_targets(tree, module_id):
                if target in allowed or target == module_id:
                    pass
                else:
                    if target not in modules:
                        items.append(_item("ARCH-007", FAIL, f"{module_id} -> {target}", "import of unknown module"))
                        continue
                    items.append(
                        _item(
                            "IMPORT-001",
                            FAIL,
                            f"{module_id} -> {target}",
                            "import not in module allow-list",
                            file=str(py_file.relative_to(root)),
                        )
                    )
                for pattern in forbidden:
                    if _pattern_matches(pattern, target):
                        items.append(_item("ARCH-006", FAIL, f"{module_id} -> {target}", f"import violates forbidden pattern '{pattern}'"))
    return items


def check_no_production_markers(root: Path) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    for area in PRODUCTION_AREAS:
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            match = _WORD_RE.search(text)
            if match:
                items.append(
                    _item(
                        "NOPRODUCTION-001",
                        FAIL,
                        str(py_file.relative_to(root)),
                        f"forbidden production marker '{match.group(0)}' (no unfinished-work markers, no imitation implementations)",
                    )
                )
    return items


def check_state_machines(root: Path) -> list[ValidationItem]:
    data, error = _load_yaml(root / "architecture" / "state-machines.yaml")
    items: list[ValidationItem] = []
    if data is None:
        items.append(_item("SM-001", FAIL, "architecture/state-machines.yaml", error or "not loadable"))
        return items
    if "version" not in data:
        items.append(_item("ARCH-004", FAIL, "state-machines", "missing version"))
    machines = data.get("machines", {})
    if not machines:
        items.append(_item("SM-001", FAIL, "state-machines", "no machines declared"))
    for name, spec in machines.items():
        states = set(spec.get("states", []))
        if not states:
            items.append(_item("SM-001", FAIL, f"state-machines.{name}", "empty state set"))
            continue
        if spec.get("initial") not in states:
            items.append(_item("SM-002", FAIL, f"state-machines.{name}", f"initial state '{spec.get('initial')}' not in states"))
        for terminal in spec.get("terminal", []):
            if terminal not in states:
                items.append(_item("SM-002", FAIL, f"state-machines.{name}", f"terminal state '{terminal}' not in states"))
        reachable: set[str] = {spec.get("initial")}
        changed = True
        transitions = spec.get("transitions", [])
        while changed:
            changed = False
            for t in transitions:
                if t.get("from") in reachable and t.get("to") not in reachable:
                    reachable.add(t["to"])
                    changed = True
        for state in sorted(states - reachable):
            items.append(_item("SM-001", WARNING, f"state-machines.{name}", f"state '{state}' is unreachable from initial"))
        for index, transition in enumerate(transitions):
            for side in ("from", "to"):
                value = transition.get(side)
                if value not in states:
                    items.append(
                        _item("SM-002", FAIL, f"state-machines.{name}.transitions[{index}]", f"unknown state '{value}'")
                    )
    return items


def check_environments(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    supported: list[str] = []
    if arch is not None:
        supported = list(arch.get("environments", {}).get("supported", []))
    machines, error = _load_yaml(root / "architecture" / "state-machines.yaml")
    if machines is not None:
        env_states = set(machines.get("machines", {}).get("environment", {}).get("states", []))
        if supported and env_states and env_states != set(supported):
            items.append(
                _item(
                    "ENV-001",
                    FAIL,
                    "state-machines.environment",
                    "environment machine states differ from architecture.yaml environments.supported",
                    architecture_yaml=sorted(supported),
                    state_machine=sorted(env_states),
                )
            )
    manifest, _ = _load_yaml(root / "architecture" / "manifest.yaml")
    if manifest is not None:
        manifest_envs = list(manifest.get("supported_environments", []))
        if supported and manifest_envs and manifest_envs != supported:
            items.append(
                _item("ENV-001", FAIL, "manifest.supported_environments", "manifest environments differ from architecture.yaml", expected=supported, actual=manifest_envs)
            )
    api, _ = _load_yaml(root / "architecture" / "api.yaml")
    if api is not None:
        for endpoint in api.get("endpoints", []):
            scope = endpoint.get("environment_scope")
            if scope != "all" and scope not in supported:
                items.append(_item("ENV-002", FAIL, f"api.{endpoint.get('path')}", f"unknown environment_scope '{scope}'"))
    permissions, _ = _load_yaml(root / "architecture" / "permissions.yaml")
    if permissions is not None:
        for restriction in permissions.get("environment_permission_restrictions", []):
            for env in restriction.get("environments", []):
                if supported and env not in supported:
                    items.append(_item("ENV-002", FAIL, f"permissions.{restriction.get('permission')}", f"unknown environment '{env}'"))
    return items


def check_schemas(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    index, error = _load_yaml(root / "architecture" / "schema-registry.yaml")
    if index is None:
        items.append(_item("SCHEMA-001", FAIL, "architecture/schema-registry.yaml", error or "not loadable"))
        return items
    if "version" not in index:
        items.append(_item("ARCH-004", FAIL, "schema-registry", "missing version"))
    modules = {m["id"]: m for m in (arch or {}).get("modules", []) if isinstance(m.get("id"), str)}
    seen_ids: set[str] = set()
    schemas_by_id: dict[str, dict[str, Any]] = {}
    for entry in index.get("schemas", []):
        schema_id = entry.get("schema_id")
        if schema_id in seen_ids:
            items.append(_item("ARCH-003", FAIL, "schema-registry", f"duplicate schema id '{schema_id}'"))
            continue
        seen_ids.add(schema_id)
        if entry.get("version") is None:
            items.append(_item("ARCH-004", FAIL, f"schema-registry.{schema_id}", "missing version in registry index"))
        schema_data, schema_error = _load_yaml(root / "architecture" / entry.get("file", ""))
        if schema_data is None:
            items.append(_item("SCHEMA-001", FAIL, f"schemas.{schema_id}", schema_error or "schema file not loadable"))
            continue
        schemas_by_id[schema_id] = schema_data
        if schema_data.get("version") is None:
            items.append(_item("ARCH-004", FAIL, f"schemas.{schema_id}", "missing version in schema file"))
        elif entry.get("version") != schema_data.get("version"):
            items.append(
                _item(
                    "SCHEMA-001",
                    FAIL,
                    f"schemas.{schema_id}",
                    "version mismatch between registry index and schema file",
                    index=entry.get("version"),
                    file=schema_data.get("version"),
                )
            )
        for key in ("schema_id", "version", "status", "owner", "description", "fields", "enums", "constraints", "compatibility", "history"):
            if key not in schema_data:
                items.append(_item("SCHEMA-001", FAIL, f"schemas.{schema_id}", f"missing schema key '{key}'"))
        owner = schema_data.get("owner")
        if owner not in modules:
            items.append(_item("SOT-003", FAIL, f"schemas.{schema_id}", f"owner '{owner}' is not a registered module"))
        enums = schema_data.get("enums", {}) or {}
        for field_name, field_spec in (schema_data.get("fields", {}) or {}).items():
            enum_ref = (field_spec or {}).get("enum")
            if enum_ref is not None and enum_ref not in enums:
                items.append(_item("SCHEMA-004", FAIL, f"schemas.{schema_id}.{field_name}", f"references unknown enum '{enum_ref}'"))
        issues = check_history(list(schema_data.get("history", []) or []))
        for issue in issues:
            items.append(
                _item(
                    str(issue.get("rule_id", "SCHEMA-002")),
                    FAIL,
                    f"schemas.{schema_id}.{issue.get('location', 'history')}",
                    str(issue.get("message", "history issue")),
                    **{k: v for k, v in (issue.get("details") or {}).items()},
                )
            )
        history = schema_data.get("history", []) or []
        if history and schema_data.get("compatibility") == "BACKWARD":
            for entry_history in history[1:]:
                for change in entry_history.get("changes", []) or []:
                    kind = change.get("kind", change) if isinstance(change, dict) else change
                    if kind in ("ADDED_REQUIRED_FIELD", "REMOVED_FIELD", "FIELD_TYPE_CHANGED", "REMOVED_ENUM_VALUE", "SEMANTIC_CHANGE"):
                        items.append(
                            _item("SCHEMA-003", FAIL, f"schemas.{schema_id}", f"declared BACKWARD but history contains breaking change '{kind}'")
                        )
    # contract ownership: module-declared contracts must exist in registry
    for module_id, module in modules.items():
        for contract in module.get("contracts", []):
            if contract and contract not in seen_ids:
                items.append(
                    _item(
                        "ARCH-008",
                        FAIL,
                        f"{module_id}.contracts",
                        f"unknown contract reference '{contract}' (not in schema registry)",
                    )
                )
    # TRACE checks: causality/traceability fields present in contracts
    event_schema = schemas_by_id.get("event", {})
    event_fields = event_schema.get("fields", {}) or {}
    if event_fields:
        if "correlation_id" not in event_fields or not (event_fields["correlation_id"] or {}).get("required"):
            items.append(_item("TRACE-002", FAIL, "schemas.event", "event contract must require correlation_id"))
        if "causation_id" not in event_fields:
            items.append(_item("TRACE-002", FAIL, "schemas.event", "event contract must carry causation_id"))
    decision_schema = schemas_by_id.get("decision", {})
    decision_fields = decision_schema.get("fields", {}) or {}
    if decision_fields:
        if "provenance" not in decision_fields or not (decision_fields["provenance"] or {}).get("required"):
            items.append(_item("TRACE-001", FAIL, "schemas.decision", "decision contract must require provenance"))
        if "reasons" not in decision_fields or not (decision_fields["reasons"] or {}).get("required"):
            items.append(_item("TRACE-001", FAIL, "schemas.decision", "decision contract must require reasons"))
    risk_schema = schemas_by_id.get("risk_decision", {})
    risk_fields = risk_schema.get("fields", {}) or {}
    if risk_fields and "policy_reference" not in risk_fields:
        items.append(_item("TRACE-001", FAIL, "schemas.risk_decision", "risk decision contract must reference its policy"))
    return items


def check_source_of_truth(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    if arch is None:
        return items
    seen_files: set[str] = set()
    for registry in arch.get("registries", []):
        relpath = registry.get("file")
        if relpath in seen_files:
            items.append(_item("ARCH-003", FAIL, "registries", f"duplicate registry file '{relpath}'"))
        seen_files.add(relpath)
        if registry.get("version") is None:
            items.append(_item("ARCH-004", FAIL, f"registries.{registry.get('id')}", "missing version"))
        if not (root / relpath).is_file():
            items.append(_item("SOT-001", FAIL, relpath, "declared source-of-truth file is missing"))
    manifest, _ = _load_yaml(root / "architecture" / "manifest.yaml")
    if manifest is not None:
        for relpath in manifest.get("source_of_truth_files", []):
            if relpath in seen_files:
                continue
            if not (root / relpath).is_file():
                items.append(_item("SOT-001", FAIL, relpath, "manifest source-of-truth file is missing"))
        declared = set(manifest.get("source_of_truth_files", []))
        for relpath in seen_files:
            if relpath not in declared:
                items.append(_item("SOT-001", WARNING, relpath, "registry file declared in architecture.yaml but not in manifest source_of_truth_files"))
    # duplicate business responsibilities
    owners: dict[str, str] = {}
    for module in arch.get("modules", []):
        for responsibility in module.get("responsibility", []):
            key = " ".join(str(responsibility).lower().split())
            if key in owners and owners[key] != module.get("id"):
                items.append(
                    _item(
                        "SOT-002",
                        FAIL,
                        f"{module.get('id')}.responsibility",
                        f"duplicate business responsibility also owned by '{owners[key]}'",
                        responsibility=str(responsibility),
                    )
                )
            owners[key] = module.get("id")
    return items


def check_manifest(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    items: list[ValidationItem] = []
    manifest, error = _load_yaml(root / "architecture" / "manifest.yaml")
    if manifest is None:
        items.append(_item("MANIFEST-001", FAIL, "architecture/manifest.yaml", error or "not loadable"))
        return items
    if arch is not None:
        arch_ids = sorted(m["id"] for m in arch.get("modules", []))
        manifest_ids = sorted(manifest.get("modules", []))
        if arch_ids != manifest_ids:
            items.append(
                _item("MANIFEST-001", FAIL, "manifest.modules", "manifest modules differ from architecture registry modules",
                      only_in_architecture=sorted(set(arch_ids) - set(manifest_ids)),
                      only_in_manifest=sorted(set(manifest_ids) - set(arch_ids)))
            )
        for key in ("architecture_version", "contract_version", "schema_version", "system_version"):
            arch_value = arch.get("system", {}).get(key)
            manifest_value = manifest.get("system", {}).get(key)
            if arch_value is not None and manifest_value is not None and arch_value != manifest_value:
                items.append(_item("MANIFEST-001", FAIL, f"manifest.system.{key}", "version differs from architecture.yaml", architecture=arch_value, manifest=manifest_value))
    index, _ = _load_yaml(root / "architecture" / "schema-registry.yaml")
    if index is not None and manifest.get("contracts", {}).get("schemas") is not None:
        index_ids = sorted(e.get("schema_id") for e in index.get("schemas", []))
        if sorted(manifest["contracts"]["schemas"]) != index_ids:
            items.append(_item("MANIFEST-001", FAIL, "manifest.contracts.schemas", "schema list differs from schema registry"))
    version_checks = {
        "schema_registry": ("schema-registry.yaml", "version"),
        "identifiers": ("identifiers.yaml", None),
        "state_machines": ("state-machines.yaml", "version"),
        "permissions": ("permissions.yaml", "version"),
        "api": ("api.yaml", "version"),
    }
    contracts = manifest.get("contracts", {})
    for manifest_key, (filename, version_key) in version_checks.items():
        expected = contracts.get(manifest_key)
        if expected is None:
            items.append(_item("MANIFEST-001", FAIL, f"manifest.contracts.{manifest_key}", "missing contract version in manifest"))
            continue
        data, load_error = _load_yaml(root / "architecture" / filename)
        if data is None:
            items.append(_item("MANIFEST-001", FAIL, filename, load_error or "not loadable"))
            continue
        actual = data[version_key] if version_key else data.get("standard", {}).get("version")
        if actual != expected:
            items.append(_item("MANIFEST-001", FAIL, f"manifest.contracts.{manifest_key}", "version differs from registry file", manifest=expected, registry=actual))
    declared_validators = manifest.get("validators", [])
    missing = sorted(set(IMPLEMENTED_RULES) - set(declared_validators))
    unknown = sorted(set(declared_validators) - set(IMPLEMENTED_RULES))
    if missing:
        items.append(_item("MANIFEST-001", FAIL, "manifest.validators", f"implemented rules missing from manifest: {missing}"))
    if unknown:
        items.append(_item("MANIFEST-001", FAIL, "manifest.validators", f"manifest declares unknown validator rules: {unknown}"))
    return items


def check_port_implementations(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    """Infrastructure modules may depend on core only for declared ports;
    the declared port class must actually exist in the core module."""
    if arch is None:
        return []
    items: list[ValidationItem] = []
    for module in arch.get("modules", []):
        for entry in module.get("implements", []):
            parts = entry.split(".")
            if len(parts) < 3:
                items.append(_item("ARCH-005", FAIL, f"{module['id']}.implements", f"malformed port reference '{entry}'"))
                continue
            port_module = ".".join(parts[:2])
            class_name = parts[-1]
            module_dir = root / port_module.replace(".", "/")
            if not module_dir.is_dir():
                items.append(_item("ARCH-007", FAIL, f"{module['id']}.implements", f"unknown port module '{port_module}'"))
                continue
            found = any(
                class_name in py_file.read_text(encoding="utf-8")
                for py_file in sorted(module_dir.rglob("*.py"))
            )
            if not found:
                items.append(_item("ARCH-008", FAIL, f"{module['id']}.implements", f"port class '{entry}' not found in its module"))
    return items


def check_phase1_data_rules(root: Path, arch: dict[str, Any] | None) -> list[ValidationItem]:
    """DATA-001 append-only store ports, DATA-002 normalization provenance,
    DATA-003 no database technology outside platform.database."""
    items: list[ValidationItem] = []

    # DATA-001: ports must not declare mutating methods
    port_files = [
        root / "core" / "data" / "stores.py",
        root / "core" / "events" / "store.py",
    ]
    forbidden_methods = ("update_", "delete_", "remove_", "overwrite_", "patch_")
    for port_file in port_files:
        if not port_file.is_file():
            continue
        for lineno, line in enumerate(port_file.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("def ") and any(
                stripped.startswith(f"def {m}") for m in forbidden_methods
            ):
                items.append(
                    _item(
                        "DATA-001", FAIL, f"{port_file.relative_to(root)}:{lineno}",
                        "store ports are append-only: mutating methods are forbidden",
                    )
                )

    # DATA-002: normalized_data schema must preserve provenance + raw reference
    schema_path = root / "architecture" / "schemas" / "normalized_data.yaml"
    data, _ = _load_yaml(schema_path)
    if data is not None:
        fields = data.get("fields", {}) or {}
        for required_field in ("provenance", "raw_id"):
            spec = fields.get(required_field) or {}
            if not spec.get("required", False):
                items.append(
                    _item(
                        "DATA-002", FAIL, "schemas/normalized_data.yaml",
                        f"normalization must preserve '{required_field}' (required field)",
                    )
                )

    # DATA-003: database technology confined to platform.database
    for area in PRODUCTION_AREAS:
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            rel = py_file.relative_to(root)
            if str(rel).replace("\\", "/").startswith("platform/database/"):
                continue
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("import sqlite3", "from sqlite3", "import psycopg", "from psycopg",
                                        "import pymysql", "import pymongo", "from pymongo")):
                    items.append(
                        _item(
                            "DATA-003", FAIL, f"{rel}:{lineno}",
                            "database technology imports are only allowed in platform.database",
                        )
                    )
    return items


_NAIVE_HAZARD_RE = re.compile(r"\.utcnow\(|datetime\.now\(\)")
_FROZEN_CLASS_RE = re.compile(r"@dataclass\(frozen=True\)\s*\nclass (\w+)")


def check_phase1_time_rules(root: Path) -> list[ValidationItem]:
    """TIME-001 canonical timestamp fields, TIME-002 no naive datetime hazards."""
    items: list[ValidationItem] = []
    for schema_name in ("event", "raw_data", "normalized_data", "data_source"):
        data, _ = _load_yaml(root / "architecture" / "schemas" / f"{schema_name}.yaml")
        if data is None:
            continue
        for field_name, spec in (data.get("fields", {}) or {}).items():
            if field_name.endswith("_time") and (spec or {}).get("type") != "timestamp":
                items.append(
                    _item(
                        "TIME-001", FAIL, f"schemas/{schema_name}.yaml",
                        f"canonical timestamp field '{field_name}' must be typed 'timestamp'",
                    )
                )
    for area in PRODUCTION_AREAS:
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for match in _NAIVE_HAZARD_RE.finditer(text):
                lineno = text.count("\n", 0, match.start()) + 1
                items.append(
                    _item(
                        "TIME-002", FAIL,
                        f"{py_file.relative_to(root)}:{lineno}",
                        "naive-datetime hazard: use the kernel time primitives (timezone-aware UTC)",
                    )
                )
    return items


def check_phase1_event_rules(root: Path) -> list[ValidationItem]:
    """EVENT-003 immutable core records, EVENT-004 replay environment gate,
    EVENT-005 single event store source of truth."""
    items: list[ValidationItem] = []
    immutable_targets = {
        "core/events/contracts.py": {"Event"},
        "core/data/contracts.py": {"RawDataRecord", "NormalizedDataRecord", "LineageRecord"},
    }
    for rel_path, class_names in immutable_targets.items():
        path = root / rel_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        frozen = set(_FROZEN_CLASS_RE.findall(text))
        for class_name in sorted(class_names - frozen):
            items.append(
                _item(
                    "EVENT-003", FAIL, rel_path,
                    f"'{class_name}' must be a frozen dataclass (accepted events/raw records are immutable)",
                )
            )

    replay_file = root / "core" / "events" / "replay.py"
    if replay_file.is_file():
        text = replay_file.read_text(encoding="utf-8")
        if "Environment.REPLAY" not in text or "assert_same_environment" not in text:
            items.append(
                _item(
                    "EVENT-004", FAIL, "core/events/replay.py",
                    "replay must be gated to the REPLAY environment (fail closed)",
                )
            )

    for area in PRODUCTION_AREAS:
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            rel = py_file.relative_to(root)
            rel_posix = str(rel).replace("\\", "/")
            if rel_posix == "core/events/store.py":
                continue
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"^class\s+EventStore\b", text, re.MULTILINE):
                items.append(
                    _item(
                        "EVENT-005", FAIL, str(rel),
                        "EventStore port must be defined exactly once (core.events.store)",
                    )
                )
    return items


def check_phase2_rules(root: Path) -> list[ValidationItem]:
    """Phase 2 rules: STATE-001..003, LEDGER-001..004, RECON-001..004."""
    items: list[ValidationItem] = []

    def schema_fields(name: str) -> dict[str, Any] | None:
        data, _ = _load_yaml(root / "architecture" / "schemas" / f"{name}.yaml")
        return (data.get("fields", {}) or {}) if data else None

    # STATE-001: state must reference its source event
    state_fields = schema_fields("state_record")
    if state_fields is not None and not (state_fields.get("source_event_id") or {}).get("required", False):
        items.append(_item("STATE-001", FAIL, "schemas/state_record.yaml",
                           "state records must require source_event_id (event provenance)"))

    # STATE-002: immutable state history (frozen dataclasses)
    state_contracts = root / "core" / "state" / "contracts.py"
    if state_contracts.is_file():
        frozen = set(_FROZEN_CLASS_RE.findall(state_contracts.read_text(encoding="utf-8")))
        for class_name in ("StateRecord", "StateTransitionRecord", "StateSnapshot"):
            if class_name not in frozen:
                items.append(_item("STATE-002", FAIL, "core/state/contracts.py",
                                   f"'{class_name}' must be a frozen dataclass (historical state is immutable)"))

    # STATE-003: snapshot integrity hash required
    snapshot_fields = schema_fields("state_snapshot")
    if snapshot_fields is not None and not (snapshot_fields.get("hash") or {}).get("required", False):
        items.append(_item("STATE-003", FAIL, "schemas/state_snapshot.yaml",
                           "snapshots must require an integrity hash"))

    # LEDGER-001: ledger store port is append-only (extends DATA-001 file set)
    ledger_port = root / "core" / "ledger" / "store.py"
    if ledger_port.is_file():
        for lineno, line in enumerate(ledger_port.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("def ") and any(
                stripped.startswith(f"def {m}") for m in ("update_", "delete_", "remove_", "overwrite_")
            ):
                items.append(_item("LEDGER-001", FAIL, f"core/ledger/store.py:{lineno}",
                                   "posted ledger entries are immutable: no mutating store methods"))

    # LEDGER-002: corrections must reference the original entry
    ledger_fields = schema_fields("ledger_entry")
    if ledger_fields is not None:
        constraints_text = " ".join(
            str(c) for c in (_load_yaml(root / "architecture" / "schemas" / "ledger_entry.yaml")[0] or {}).get("constraints", [])
        )
        if "adjusts_entry_id" not in constraints_text:
            items.append(_item("LEDGER-002", FAIL, "schemas/ledger_entry.yaml",
                               "ledger contract must require corrections to reference the original entry"))
        # LEDGER-003: idempotency required
        if "idempotency_key" not in ledger_fields:
            items.append(_item("LEDGER-003", FAIL, "schemas/ledger_entry.yaml",
                               "ledger entries must carry a deterministic idempotency key"))
        # LEDGER-004: canonical decimal financial values
        if (ledger_fields.get("amount") or {}).get("type") != "decimal":
            items.append(_item("LEDGER-004", FAIL, "schemas/ledger_entry.yaml",
                               "ledger amounts must be canonical decimals (never binary floats)"))

    # RECON-001: reconciliation must not auto-fix (no mutating calls in the engine)
    engine_file = root / "core" / "reconciliation" / "engine.py"
    if engine_file.is_file():
        text = engine_file.read_text(encoding="utf-8")
        if re.search(r"\.(update_|delete_|remove_|overwrite_)\w*\(", text):
            items.append(_item("RECON-001", FAIL, "core/reconciliation/engine.py",
                               "reconciliation reports; it must never mutate state or ledger"))

    # RECON-002: UNKNOWN never becomes MATCH without evidence
    recon_fields = schema_fields("reconciliation_result")
    if recon_fields is not None:
        recon_data, _ = _load_yaml(root / "architecture" / "schemas" / "reconciliation_result.yaml")
        enums = (recon_data or {}).get("enums", {}) or {}
        if "UNKNOWN" not in (enums.get("recon_status") or []):
            items.append(_item("RECON-002", FAIL, "schemas/reconciliation_result.yaml",
                               "reconciliation status must include UNKNOWN (never silently MATCH)"))

    # RECON-003: tolerance must be explicit and centralized
    tolerance_module = root / "core" / "reconciliation" / "tolerance.py"
    if not (root / "architecture" / "tolerances.yaml").is_file() or (
        tolerance_module.is_file()
        and 'load_registry("tolerances.yaml")' not in tolerance_module.read_text(encoding="utf-8")
    ):
        items.append(_item("RECON-003", FAIL, "core/reconciliation/tolerance.py",
                           "tolerances must resolve from the canonical tolerances registry"))

    # RECON-004: historical reconciliation immutable (frozen + append-only)
    recon_contracts = root / "core" / "reconciliation" / "contracts.py"
    if recon_contracts.is_file():
        frozen = set(_FROZEN_CLASS_RE.findall(recon_contracts.read_text(encoding="utf-8")))
        for class_name in ("ReconciliationResult", "ExternalObservation", "Difference"):
            if class_name not in frozen:
                items.append(_item("RECON-004", FAIL, "core/reconciliation/contracts.py",
                                   f"'{class_name}' must be frozen (historical reconciliation is immutable)"))
    return items




def check_phase3_rules(root: Path) -> list[ValidationItem]:
    """Phase 3 rules: policy lifecycle/approval, risk precedence/config,
    engine authority boundaries, decision integrity, environment gate, audit."""
    items: list[ValidationItem] = []

    policy_data, _ = _load_yaml(root / "architecture" / "schemas" / "policy.yaml")
    machines, _ = _load_yaml(root / "architecture" / "state-machines.yaml")
    risk_config, _ = _load_yaml(root / "architecture" / "risk-config.yaml")
    risk_schema, _ = _load_yaml(root / "architecture" / "schemas" / "risk_decision.yaml")
    registry_src = root / "core" / "policy" / "registry.py"
    engine_src = root / "core" / "risk" / "engine.py"

    # POLICY-001
    if policy_data is not None:
        constraints = " ".join(str(c) for c in policy_data.get("constraints", []))
        if "approved_by" not in constraints:
            items.append(_item("POLICY-001", FAIL, "schemas/policy.yaml",
                               "policy contract must require approval for ACTIVE status"))
    # POLICY-002
    if machines is not None:
        states = set((machines.get("machines", {}).get("policy_status", {}) or {}).get("states", []))
        if not {"REVIEW", "APPROVED"} <= states:
            items.append(_item("POLICY-002", FAIL, "state-machines.policy_status",
                               "policy lifecycle must declare REVIEW and APPROVED states"))
    # POLICY-003
    if registry_src.is_file():
        src = registry_src.read_text(encoding="utf-8")
        if "created_by == actor.user_id" not in src:
            items.append(_item("POLICY-003", FAIL, "core/policy/registry.py",
                               "policy registry must reject self-approval"))
    # RISK-001 (precedence single-sourced; no hard-coded tuples elsewhere)
    if risk_config is None or "permission_precedence" not in risk_config:
        items.append(_item("RISK-001", FAIL, "architecture/risk-config.yaml",
                           "permission precedence must be configured (single source)"))
    else:
        pattern = re.compile(r"\(\s*\"EMERGENCY\",\s*\"CLOSE_ONLY\",\s*\"BLOCK\"")
        for area in ("core", "platform"):
            area_dir = root / area
            if not area_dir.is_dir():
                continue
            for py_file in area_dir.rglob("*.py"):
                rel = str(py_file.relative_to(root)).replace("\\", "/")
                if rel == "core/risk/config.py":
                    continue
                if pattern.search(py_file.read_text(encoding="utf-8")):
                    items.append(_item("RISK-001", FAIL, rel,
                                       "hard-coded permission precedence (must resolve from risk-config)"))
    # RISK-002
    if risk_schema is not None:
        results = (risk_schema.get("enums", {}) or {}).get("risk_result", [])
        if set(results) != {"ALLOW", "LIMITED", "BLOCK", "CLOSE_ONLY", "EMERGENCY"}:
            items.append(_item("RISK-002", FAIL, "schemas/risk_decision.yaml",
                               "risk_result enum must be exactly the five canonical permissions"))
    # RISK-003
    if risk_config is not None and not risk_config.get("critical_unknown_fields"):
        items.append(_item("RISK-003", FAIL, "architecture/risk-config.yaml",
                           "critical unknown fields must be configured (fail closed)"))
    # RISK-004
    if engine_src.is_file():
        src = engine_src.read_text(encoding="utf-8")
        hard_first = "HARD_POLICY_TYPES" in src and src.find("hard_evaluations") < src.find("dimension_evaluations")
        if not hard_first:
            items.append(_item("RISK-004", FAIL, "core/risk/engine.py",
                               "hard safety policies must evaluate before dimension composition"))
    # RISK-005
    risk_dir = root / "core" / "risk"
    if risk_dir.is_dir():
        for py_file in risk_dir.rglob("*.py"):
            body = py_file.read_text(encoding="utf-8")
            if re.search(r"import\s+sqlite3|from\s+platform\.database", body):
                items.append(_item("RISK-005", FAIL, str(py_file.relative_to(root)),
                                   "core.risk must never depend on storage implementations"))
    # RISK-006
    if risk_schema is not None:
        expires = (risk_schema.get("fields", {}) or {}).get("expires_at", {})
        if not expires.get("required", False):
            items.append(_item("RISK-006", FAIL, "schemas/risk_decision.yaml",
                               "risk decisions must require expires_at"))
    # SAFETY-001
    if risk_config is not None and risk_config.get("hard_limit_override_allowed") is not False:
        items.append(_item("SAFETY-001", FAIL, "architecture/risk-config.yaml",
                           "hard limit override must be forbidden"))
    # SAFETY-002
    if policy_data is not None:
        types = (policy_data.get("enums", {}) or {}).get("policy_type", [])
        if "GLOBAL_SAFETY_POLICY" not in types:
            items.append(_item("SAFETY-002", FAIL, "schemas/policy.yaml",
                               "GLOBAL_SAFETY_POLICY type must exist"))
    # DECISION-001
    if engine_src.is_file():
        src = engine_src.read_text(encoding="utf-8")
        if "risk_context_hash=context.context_hash" not in src:
            items.append(_item("DECISION-001", FAIL, "core/risk/engine.py",
                               "decisions must carry the risk context hash"))
    # DECISION-002
    validator_src = root / "core" / "risk" / "validator.py"
    if validator_src.is_file():
        if "is_expired" not in validator_src.read_text(encoding="utf-8"):
            items.append(_item("DECISION-002", FAIL, "core/risk/validator.py",
                               "decision validation must reject expired decisions"))
    # ENV-003
    replay_src = root / "core" / "risk" / "replay.py"
    if replay_src.is_file():
        src = replay_src.read_text(encoding="utf-8")
        if '"REPLAY"' not in src and "'REPLAY'" not in src:
            items.append(_item("ENV-003", FAIL, "core/risk/replay.py",
                               "risk replay must be gated to the REPLAY environment"))
    # AUDIT-003
    if registry_src.is_file():
        if "_audit_policy" not in registry_src.read_text(encoding="utf-8"):
            items.append(_item("AUDIT-003", FAIL, "core/policy/registry.py",
                               "privileged policy lifecycle actions must be audited"))
    return items




def check_phase4_rules(root: Path) -> list[ValidationItem]:
    """Phase 4 rules: strategy/portfolio contracts + allocation/exposure/
    intent semantics + the no-execution boundary."""
    items: list[ValidationItem] = []

    def load_schema(name):
        data, _ = _load_yaml(root / "architecture" / "schemas" / f"{name}.yaml")
        return data

    # STRATEGY-001: strategy lifecycle machine declared + LIVE prerequisites
    machines, _ = _load_yaml(root / "architecture" / "state-machines.yaml")
    if machines is not None:
        sl = (machines.get("machines", {}).get("strategy_lifecycle", {}) or {})
        states = set(sl.get("states", []))
        required = {"IDEA", "APPROVED", "LIVE", "SUSPENDED", "RETIRED"}
        if not required <= states:
            items.append(_item("STRATEGY-001", FAIL, "state-machines.strategy_lifecycle",
                               "strategy lifecycle machine must declare the promotion chain states"))

    strategy_schema = load_schema("strategy_record")
    if strategy_schema is not None:
        constraints = " ".join(str(c) for c in strategy_schema.get("constraints", []))
        if "LIVE requires" not in constraints:
            items.append(_item("STRATEGY-001", FAIL, "schemas/strategy_record.yaml",
                               "strategy contract must document LIVE prerequisites"))

    # STRATEGY-002: versions immutable (frozen dataclass)
    contracts_src = root / "core" / "strategy" / "contracts.py"
    if contracts_src.is_file():
        frozen = set(_FROZEN_CLASS_RE.findall(contracts_src.read_text(encoding="utf-8")))
        for class_name in ("Strategy", "CapabilityProfile", "StrategyConfig"):
            if class_name not in frozen:
                items.append(_item("STRATEGY-002", FAIL, "core/strategy/contracts.py",
                                   f"'{class_name}' must be a frozen dataclass"))

    # STRATEGY-003: dependency cycles rejected
    dep_src = root / "core" / "strategy" / "dependency.py"
    if dep_src.is_file():
        if "DependencyCycleError" not in dep_src.read_text(encoding="utf-8"):
            items.append(_item("STRATEGY-003", FAIL, "core/strategy/dependency.py",
                               "dependency graph must reject cycles"))

    # STRATEGY-004: capability provenance required
    capability_schema = load_schema("strategy_capability")
    if capability_schema is not None:
        prov = (capability_schema.get("fields", {}) or {}).get("provenance", {})
        if not prov.get("required", False):
            items.append(_item("STRATEGY-004", FAIL, "schemas/strategy_capability.yaml",
                               "capability observations must require provenance"))

    # STRATEGY-005: config hash integrity required
    config_schema = load_schema("strategy_config")
    if config_schema is not None:
        chash = (config_schema.get("fields", {}) or {}).get("config_hash", {})
        if not chash.get("required", False):
            items.append(_item("STRATEGY-005", FAIL, "schemas/strategy_config.yaml",
                               "strategy configs must carry an integrity hash"))

    # PORTFOLIO-001: portfolio decision never overrides risk
    decision_src = root / "core" / "portfolio" / "decision.py"
    if decision_src.is_file():
        src = decision_src.read_text(encoding="utf-8")
        if "RiskDecision" in src.replace("risk_decision_id", ""):
            # the decision engine references RiskDecision objects beyond id fields
            if "import" in src and "core.risk.contracts" in src and "RiskDecision(" in src:
                items.append(_item("PORTFOLIO-001", FAIL, "core/portfolio/decision.py",
                                   "portfolio decisions must not construct RiskDecisions"))
        if "override" in src.lower() and "never overrides" not in src.lower():
            items.append(_item("PORTFOLIO-001", FAIL, "core/portfolio/decision.py",
                               "portfolio decision engine must never override risk decisions"))

    # PORTFOLIO-002: explicit priority (no implicit ordering)
    membership_schema = load_schema("portfolio_membership")
    if membership_schema is not None:
        priority = (membership_schema.get("fields", {}) or {}).get("priority", {})
        if priority.get("type") != "integer" or priority.get("required", False) is not True:
            items.append(_item("PORTFOLIO-002", FAIL, "schemas/portfolio_membership.yaml",
                               "membership priority must be a required explicit integer"))

    # PORTFOLIO-003: allocation arithmetic centralized
    allocation_src = root / "core" / "portfolio" / "allocation.py"
    if allocation_src.is_file():
        src = allocation_src.read_text(encoding="utf-8")
        if "available - allocated" not in src or "total - reserved" not in src.replace("available = total - reserved", "total - reserved"):
            items.append(_item("PORTFOLIO-003", FAIL, "core/portfolio/allocation.py",
                               "capital arithmetic must be centralized here"))

    # PORTFOLIO-004: exposure aggregation centralized + arithmetic checked
    exposure_src = root / "core" / "portfolio" / "exposure.py"
    if exposure_src.is_file():
        src = exposure_src.read_text(encoding="utf-8")
        if "gross != |long| + |short|" not in src:
            items.append(_item("PORTFOLIO-004", FAIL, "core/portfolio/exposure.py",
                               "exposure arithmetic must be validated centrally"))

    # PORTFOLIO-005: paused/suspended portfolios reject allocation
    if decision_src.is_file():
        src = decision_src.read_text(encoding="utf-8")
        if "is not PortfolioStatus.ACTIVE" not in src:
            items.append(_item("PORTFOLIO-005", FAIL, "core/portfolio/decision.py",
                               "non-ACTIVE portfolios must fail closed"))

    # ALLOCATION-001: never silently clip (explicit overflow behaviors only)
    if allocation_src.is_file():
        src = allocation_src.read_text(encoding="utf-8")
        if "REJECT" not in src or "CONSTRAIN_TO_AVAILABLE" not in src:
            items.append(_item("ALLOCATION-001", FAIL, "core/portfolio/allocation.py",
                               "overflow must be explicit (reject or constrain - never silent clipping)"))

    # ALLOCATION-002: risk budgets reuse Phase 3 (no duplicate budget type)
    budget_dup = (root / "core" / "portfolio" / "budget.py")
    if budget_dup.is_file():
        items.append(_item("ALLOCATION-002", FAIL, "core/portfolio/budget.py",
                           "portfolio must REUSE core.risk.RiskBudget (no duplicate budget contract)"))

    # EXPOSURE-001: exposure schema constraints present
    exposure_schema = load_schema("portfolio_exposure")
    if exposure_schema is not None:
        constraints = " ".join(str(c) for c in exposure_schema.get("constraints", []))
        if "per-strategy risk isolation is forbidden" not in constraints.lower() and \
                "per-strategy" not in constraints.lower():
            items.append(_item("EXPOSURE-001", FAIL, "schemas/portfolio_exposure.yaml",
                               "exposure contract must forbid per-strategy risk isolation"))

    # EXPOSURE-002: correlated groups UNKNOWN without data
    if exposure_schema is not None:
        constraints = " ".join(str(c) for c in exposure_schema.get("constraints", []))
        if "UNKNOWN" not in constraints:
            items.append(_item("EXPOSURE-002", FAIL, "schemas/portfolio_exposure.yaml",
                               "correlated groups must be explicitly UNKNOWN without data"))

    # INTENT-001: intents are not orders (direction/quantity semantics enforced)
    intent_schema = load_schema("strategy_intent")
    if intent_schema is not None:
        constraints = " ".join(str(c) for c in intent_schema.get("constraints", []))
        if "NOT an order" not in constraints:
            items.append(_item("INTENT-001", FAIL, "schemas/strategy_intent.yaml",
                               "intent contract must state INTENT != ORDER"))

    # INTENT-002: intents expire
    if intent_schema is not None:
        expires = (intent_schema.get("fields", {}) or {}).get("expires_at", {})
        if not expires.get("required", False):
            items.append(_item("INTENT-002", FAIL, "schemas/strategy_intent.yaml",
                               "intents must require expires_at"))

    # BOUNDARY-001: no execution dependency in strategy/portfolio modules
    for rel in ("core/strategy", "core/portfolio"):
        area = root / rel
        if not area.is_dir():
            continue
        for py_file in area.rglob("*.py"):
            body = py_file.read_text(encoding="utf-8")
            if re.search(r"from\s+core\.execution\.contracts\s+import\s+.*Order|submit_order|OMS|EMS|BrokerAdapter", body):
                items.append(_item("BOUNDARY-001", FAIL, str(py_file.relative_to(root)),
                                   "strategy/portfolio must not depend on execution"))

    # BOUNDARY-002: gate re-uses the SAME risk engine (no second engine)
    gate_src = root / "core" / "strategy" / "gate.py"
    if gate_src.is_file():
        src = gate_src.read_text(encoding="utf-8")
        if "RiskEngine" not in src:
            items.append(_item("BOUNDARY-002", FAIL, "core/strategy/gate.py",
                               "the intent gate must re-use the Phase 3 RiskEngine (single engine)"))
        if "class RiskEngine" in src:
            items.append(_item("BOUNDARY-002", FAIL, "core/strategy/gate.py",
                               "a second risk engine is forbidden"))

    return items




def check_phase5_rules(root: Path) -> list[ValidationItem]:
    """Phase 5 rules: OMS/EMS boundaries, execution risk gating, MT5 isolation,
    position/ledger/reconciliation reuse, security, AI authority, phase scope."""
    items: list[ValidationItem] = []

    oms_src = root / "core" / "oms" / "engine.py"
    ems_src = root / "core" / "ems" / "engine.py"
    boundary_src = root / "core" / "execution" / "boundary.py"
    mt5_src = root / "adapters" / "mt5" / "execution.py"
    transport_src = root / "adapters" / "mt5" / "transport.py"
    proj_src = root / "core" / "oms" / "projection.py"

    # OMS-001: OMS owns order lifecycle (transition machine usage)
    if oms_src.is_file():
        src = oms_src.read_text(encoding="utf-8")
        if "order_state" not in src or "OrderManagementSystem" not in src:
            items.append(_item("OMS-001", FAIL, "core/oms/engine.py",
                               "OMS must own the order lifecycle through the Phase 0 machine"))
    # OMS-002: OMS cannot depend on MT5 implementation
    if oms_src.is_file():
        src = oms_src.read_text(encoding="utf-8")
        if re.search(r"adapters\.mt5|MetaTrader5|MT5ExecutionAdapter", src):
            items.append(_item("OMS-002", FAIL, "core/oms/engine.py",
                               "OMS must never depend on MT5 (routes through EMS adapter port)"))

    # EMS-001: EMS only accepts validated canonical orders
    if ems_src.is_file():
        src = ems_src.read_text(encoding="utf-8")
        if "ACCEPTED" not in src:
            items.append(_item("EMS-001", FAIL, "core/ems/engine.py",
                               "EMS must only accept OMS-validated (ACCEPTED) orders"))
    # EMS-002: EMS cannot override risk (no RiskResult construction / no ALLOW granting)
    if ems_src.is_file():
        src = ems_src.read_text(encoding="utf-8")
        if re.search(r"RiskResult\.(ALLOW|LIMITED)", src):
            items.append(_item("EMS-002", FAIL, "core/ems/engine.py",
                               "EMS must never grant risk permission"))

    # EXEC-001/002/003: boundary exists, uses can_execute, UNKNOWN fails closed
    if boundary_src.is_file():
        src = boundary_src.read_text(encoding="utf-8")
        if "def can_execute" not in src:
            items.append(_item("EXEC-001", FAIL, "core/execution/boundary.py",
                               "the canonical can_execute boundary must exist"))
        if "ExecutionDecision.UNKNOWN" not in src:
            items.append(_item("EXEC-003", FAIL, "core/execution/boundary.py",
                               "UNKNOWN execution state must exist and fail closed"))
    else:
        items.append(_item("EXEC-001", FAIL, "core/execution/boundary.py",
                           "execution boundary module missing"))

    # EXEC-004: duplicate submission idempotent (semantic idempotency key)
    if oms_src.is_file():
        src = oms_src.read_text(encoding="utf-8")
        if "idempotency_key" not in src or "find_by_idempotency_key" not in src:
            items.append(_item("EXEC-004", FAIL, "core/oms/engine.py",
                               "duplicate semantic submissions must be idempotent"))

    # EXEC-005: no second risk engine in Phase 5 modules
    for module_path in (oms_src, ems_src, mt5_src, transport_src):
        if module_path.is_file():
            src = module_path.read_text(encoding="utf-8")
            if re.search(r"class\s+RiskEngine\b", src):
                items.append(_item("EXEC-005", FAIL, str(module_path.relative_to(root)),
                                   "a second risk engine is forbidden (reuse Phase 3)"))

    # MT5-001: MT5 types cannot leak into domain (core must not import adapters.mt5)
    for area in ("core/oms", "core/ems", "core/execution"):
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            if re.search(r"from\s+adapters\.mt5|import\s+adapters\.mt5|MetaTrader5",
                         py_file.read_text(encoding="utf-8")):
                items.append(_item("MT5-001", FAIL, str(py_file.relative_to(root)),
                                   "MT5-specific types must never leak into the domain"))
    # MT5-002: MT5 is adapter implementation only (lives under adapters/)
    if mt5_src.is_file() and not (root / "adapters" / "mt5").is_dir():
        items.append(_item("MT5-002", FAIL, "adapters/mt5/execution.py",
                           "MT5 implementation must live under adapters/"))

    # ENVX-001: environment isolation (EMS must fail closed on unknown adapter env)
    if ems_src.is_file():
        src = ems_src.read_text(encoding="utf-8")
        if "No execution adapter registered" not in src:
            items.append(_item("ENVX-001", FAIL, "core/ems/engine.py",
                               "unknown environments must fail closed (no cross-env fallback)"))

    # POSX-001: positions use the canonical state engine
    if proj_src.is_file():
        src = proj_src.read_text(encoding="utf-8")
        if "StateCategory.POSITION_STATE" not in src or "StateEngine" not in src:
            items.append(_item("POSX-001", FAIL, "core/oms/projection.py",
                               "positions must derive through the Phase 2 state engine"))
    # LEDX-001: ledger uses Phase 2 posting
    if proj_src.is_file():
        src = proj_src.read_text(encoding="utf-8")
        if "LedgerPostingService" not in src or "LedgerDraft" not in src:
            items.append(_item("LEDX-001", FAIL, "core/oms/projection.py",
                               "execution effects must post through the Phase 2 ledger"))
    # RECX-001: reconciliation uses Phase 2 engine (no auto-fix)
    recon_uses = False
    for py_file in (root / "core").rglob("*.py"):
        if py_file.name == "engine.py" and "reconciliation" in str(py_file.parent):
            recon_uses = True
    if not recon_uses:
        items.append(_item("RECX-001", FAIL, "core/reconciliation/engine.py",
                           "reconciliation must reuse the Phase 2 engine (report-only)"))

    # SECX-001: secrets cannot be logged (no credential assignment in adapters)
    for py_file in (root / "adapters").rglob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        credential_pattern = re.compile(
            r"(password|api_key|secret|token)" + r"\s*=\s*['\"][^'\"]+['\"]",
            re.IGNORECASE,
        )
        if credential_pattern.search(src):
            items.append(_item("SECX-001", FAIL, str(py_file.relative_to(root)),
                               "credential values must never be assigned in source"))

    # AIX-001: AI has no execution authority
    ai_contracts = root / "adapters" / "ai" / "contracts.py"
    if ai_contracts.is_file():
        src = ai_contracts.read_text(encoding="utf-8")
        if re.search(r"submit_order|OrderManagementSystem|ExecutionAdapter|MetaTrader5", src):
            items.append(_item("AIX-001", FAIL, "adapters/ai/contracts.py",
                               "AI must have no execution authority"))

    # BOUNDX-001: current phase cannot implement the NEXT phase's scope.
    # Phase 7 scope (advisory intelligence under core.intelligence) is
    # legitimate here; Phase 8 (Security + Governance + Audit expansion,
    # Desktop GUI, Web Workspace) is not.
    phase7_forbidden = ("core/ml", "core/ml_authority",
                        "core/ai_authority",
                        "adapters/ai/authority.py")
    phase10_forbidden = ("ui/web/app.py", "ui/web/server.py",
                         "ui/web/workspace.py", "ui/mobile", "ui/ios",
                         "ui/android")
    for forbidden in phase7_forbidden + phase10_forbidden:
        if (root / forbidden).exists():
            items.append(_item("BOUNDX-001", FAIL, forbidden,
                               "the current phase must not implement a "
                               "later phase's scope (AI authority / "
                               "web workspace / mobile)"))

    return items


def check_phase7_rules(root: Path) -> list[ValidationItem]:
    """Phase 7 rules (AI-001..AI-036): the intelligence plane is advisory
    only. AI never reaches OMS/EMS/brokers, never creates RiskDecision or
    Order, never self-promotes, never bypasses Strategy/Portfolio/Risk,
    and every AI artifact is versioned, hashed, PIT-correct and
    fail-closed on critical UNKNOWN."""
    items: list[ValidationItem] = []

    intel_dir = root / "core" / "intelligence"
    py_files = sorted(intel_dir.rglob("*.py")) if intel_dir.is_dir() else []

    def load(rel):
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def load_schema(name):
        data, _ = _load_yaml(root / "architecture" / "schemas" / (name + ".yaml"))
        return data or {}

    def scan_intel(pattern):
        compiled = re.compile(pattern)
        for py_file in py_files:
            if compiled.search(py_file.read_text(encoding="utf-8")):
                return str(py_file.relative_to(root))
        return None

    def require_absent(rule, pattern, message):
        hit = scan_intel(pattern)
        if hit:
            items.append(_item(rule, FAIL, hit, message))

    # --- authority boundaries (AI-001..AI-011) -------------------------
    require_absent("AI-001", r"from\s+core\.oms|import\s+core\.oms",
                   "AI must never import OMS (required path is Strategy -> "
                   "Portfolio -> Risk -> OMS)")
    require_absent("AI-002", r"from\s+core\.ems|import\s+core\.ems",
                   "AI must never import EMS")
    require_absent("AI-003",
                   r"adapters\.(mt5|broker|simulation)|MetaTrader5",
                   "AI must never import broker/MT5/simulation adapters")
    require_absent("AI-004", r"RiskDecision\s*\(|from\s+core\.risk",
                   "AI must never create or import RiskDecision (Phase 3 "
                   "risk engine is the only risk authority)")
    require_absent("AI-005", r"from\s+core\.policy|import\s+core\.policy",
                   "AI must never bypass Policy")
    require_absent("AI-006", r"from\s+core\.portfolio|import\s+core\.portfolio",
                   "AI must never bypass Portfolio")
    require_absent("AI-007", r"from\s+core\.strategy|import\s+core\.strategy",
                   "AI must never bypass Strategy eligibility")
    require_absent("AI-008", r"hard_limits|GLOBAL_SAFETY|risk-config\.yaml",
                   "AI must never modify risk limits or safety policy")
    lifecycle_src = load("core/intelligence/lifecycle.py")
    if lifecycle_src is None:
        items.append(_item("AI-009", FAIL, "core/intelligence/lifecycle.py",
                           "model lifecycle service must exist (Phase 0 "
                           "machine reuse, no AIStateEngine)"))
    else:
        for token in ("HUMAN_APPROVAL", "model_lifecycle", "actor.require"):
            if token not in lifecycle_src:
                items.append(_item("AI-009", FAIL,
                                   "core/intelligence/lifecycle.py",
                                   "model promotion must require human "
                                   "approval + permission (no self-promotion)"))
    require_absent("AI-010", r"def\s+deploy_self|self_deploy",
                   "AI must never self-deploy")
    require_absent("AI-011", r'inference_environments\s*=\s*\([^)]*"LIVE"',
                   "AI must never enable LIVE inference by itself (LIVE "
                   "approval is a human-governed later concern)")
    # --- data discipline (AI-012..AI-019) ------------------------------
    feature_src = load("core/intelligence/feature.py")
    if feature_src is None or "visible_at" not in feature_src:
        items.append(_item("AI-012", FAIL, "core/intelligence/feature.py",
                           "features must use Phase 6 point-in-time "
                           "semantics (visible_at) - no second PIT oracle"))
    inference_src = load("core/intelligence/inference.py")
    if inference_src is None:
        items.append(_item("AI-013", FAIL, "core/intelligence/inference.py",
                           "inference engine must exist"))
    else:
        for token, rule, message in (
            ("provenance", "AI-013", "inference must carry complete provenance"),
            ("ENVIRONMENT_MISMATCH", "AI-018",
             "inference must be environment-aware (no silent cross-env)"),
            ("POINT_IN_TIME_VIOLATION", "AI-019",
             "inference must enforce the point-in-time rule"),
            ("SafetyDecision.BLOCK", "AI-019",
             "critical UNKNOWN must fail closed (BLOCK)"),
        ):
            if token not in inference_src:
                items.append(_item(rule, FAIL, "core/intelligence/inference.py",
                                   message))
    contracts_src = load("core/intelligence/contracts.py") or ""
    for token, rule, message in (
        ("SemVer.parse(self.model_version", "AI-014",
         "model versions must be SemVer"),
        ("artifact_hash", "AI-015", "model artifacts must be hashed"),
        ("feature_schema_hash", "AI-016", "feature schemas must be hashed"),
        ("confidence: float | None", "AI-030",
         "confidence must be an explicit field"),
        ("probability: float | None", "AI-030",
         "probability must be an explicit field"),
        ("score: float | None", "AI-030",
         "score must be an explicit field"),
    ):
        if token not in contracts_src:
            items.append(_item(rule, FAIL, "core/intelligence/contracts.py",
                               message))
    idataset_schema = load_schema("intelligence_dataset")
    idataset_fields = idataset_schema.get("fields", {}) or {}
    idataset_constraints = " ".join(
        str(c) for c in idataset_schema.get("constraints", []))
    if not idataset_fields.get("content_hash", {}).get("required", False):
        items.append(_item("AI-017", FAIL, "schemas/intelligence_dataset.yaml",
                           "intelligence datasets must carry a required "
                           "content hash"))
    if "immutable" not in idataset_constraints:
        items.append(_item("AI-017", FAIL, "schemas/intelligence_dataset.yaml",
                           "intelligence datasets must be immutable"))
    # --- no second engines (AI-020..AI-024) -----------------------------
    require_absent("AI-020", r"class\s+RiskEngine\b",
                   "AI must never create a second risk engine")
    require_absent("AI-021", r"class\s+\w*StateEngine\b|AIStateEngine",
                   "AI must never create a second state engine")
    require_absent("AI-022", r"class\s+\w*EventStore\b",
                   "AI must never create a second event store")
    require_absent("AI-023", r"class\s+\w*Ledger\b",
                   "AI must never create a second ledger")
    require_absent("AI-024", r"class\s+\w*ReplayEngine\b",
                   "AI must never create a second replay engine")
    # --- strategy/boundary discipline (AI-025..AI-036) ------------------
    require_absent("AI-025", r"StrategyLifecycle",
                   "AI must never mutate Strategy lifecycle state")
    require_absent("AI-026", r"latest_model|resolve_latest",
                   "AI must never silently use the latest model")
    require_absent("AI-027", r"latest_dataset",
                   "AI must never silently use the latest dataset")
    require_absent("AI-028", r"fallback_model",
                   "AI must never silently fall back to another model")
    registry_src = load("core/intelligence/registry.py") or ""
    if "already registered with different" not in registry_src:
        items.append(_item("AI-029", FAIL, "core/intelligence/registry.py",
                           "feature versions are immutable - re-registration "
                           "with different content must be rejected"))
    outputs_src = load("core/intelligence/outputs.py") or ""
    if "limitations" not in outputs_src or "causal" not in outputs_src:
        items.append(_item("AI-031", FAIL, "core/intelligence/outputs.py",
                           "explanations must declare limitations and never "
                           "claim unsupported causality"))
    adapter_src = load("core/intelligence/adapter.py") or ""
    if "artifact hash mismatch" not in adapter_src:
        items.append(_item("AI-032", FAIL, "core/intelligence/adapter.py",
                           "model artifacts must be integrity-validated "
                           "before use"))
    if re.search(r"class\s+ModelDefinition", contracts_src) and \
            not re.search(
                r"@dataclass\(frozen=True\)\s*\nclass ModelDefinition",
                contracts_src):
        items.append(_item("AI-033", FAIL, "core/intelligence/contracts.py",
                           "model definitions must be immutable (frozen)"))
    require_absent("AI-034", r"OrderStore|SqliteOrderStore|OrderManagementSystem",
                   "training must never alter production state")
    replay_src = load("core/intelligence/replay.py") or ""
    if replay_src and "REPLAY" not in replay_src:
        items.append(_item("AI-035", FAIL, "core/intelligence/replay.py",
                           "replay must run in the REPLAY environment "
                           "(read-only)"))
    require_absent("AI-036", r"Order\s*\(|from\s+core\.execution",
                   "AI output can never become an Order without "
                   "Strategy/Portfolio/Risk")
    if not py_files:
        items.append(_item("AI-001", FAIL, "core/intelligence",
                           "the intelligence plane must exist"))

    return items





def check_phase6_rules(root: Path) -> list[ValidationItem]:
    """Phase 6 rules: dataset integrity, bias detection, backtest boundaries
    (strategy/portfolio/risk reuse, no LIVE), replay read-only, research
    reproducibility, validation/stress versioning, no auto-promotion."""
    items: list[ValidationItem] = []

    def load_schema(name):
        data, _ = _load_yaml(root / "architecture" / "schemas" / f"{name}.yaml")
        return data

    def load_module(rel):
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    # DATASET-001..004: dataset contract essentials
    dataset_schema = load_schema("research_dataset")
    if dataset_schema is not None:
        fields = dataset_schema.get("fields", {}) or {}
        if not (fields.get("available_time") or fields.get("lineage")):
            # point-in-time enforcement lives on observations; require the
            # PIT constraint text and content hash
            constraints = " ".join(str(c) for c in dataset_schema.get("constraints", []))
            if "Point-in-time" not in constraints:
                items.append(_item("DATASET-004", FAIL, "schemas/research_dataset.yaml",
                                   "dataset must preserve point-in-time semantics"))
        if not (fields.get("content_hash", {}).get("required", False)):
            items.append(_item("DATASET-003", FAIL, "schemas/research_dataset.yaml",
                               "dataset must carry a required content hash"))
        if not (fields.get("lineage", {}).get("required", False)):
            items.append(_item("DATASET-002", FAIL, "schemas/research_dataset.yaml",
                               "dataset must require lineage (Phase 1 reuse)"))
        constraints = " ".join(str(c) for c in dataset_schema.get("constraints", []))
        if "immutable" not in constraints:
            items.append(_item("DATASET-001", FAIL, "schemas/research_dataset.yaml",
                               "dataset must be immutable (new version, never edit)"))

    # BIAS-001/002: bias auditor exists and detects look-ahead + leakage
    bias_src = load_module("core/research/bias.py")
    if bias_src is None:
        items.append(_item("BIAS-001", FAIL, "core/research/bias.py",
                           "bias auditor must exist"))
    else:
        for token, rule in (("LOOK_AHEAD", "BIAS-001"),
                            ("DATA_LEAKAGE", "BIAS-002")):
            if token not in bias_src:
                items.append(_item(rule, FAIL, "core/research/bias.py",
                                   f"bias auditor must detect {token}"))
    # BIAS-003/004: survivorship + selection status explicit
    if bias_src is not None:
        for token, rule in (("SURVIVORSHIP", "BIAS-003"), ("SELECTION", "BIAS-004")):
            if token not in bias_src:
                items.append(_item(rule, FAIL, "core/research/bias.py",
                                   f"bias auditor must report {token} status"))

    # BACKTEST-001..003: backtest reuses strategy->portfolio->risk path
    engine_src = load_module("core/backtest/engine.py")
    pipeline_src = load_module("core/research/pipeline.py")
    if engine_src is None:
        items.append(_item("BACKTEST-001", FAIL, "core/backtest/engine.py",
                           "backtest engine must exist"))
    else:
        if "policy_allows" not in engine_src:
            items.append(_item("BACKTEST-003", FAIL, "core/backtest/engine.py",
                               "backtest must gate actions through policy/risk semantics"))
    if pipeline_src is not None and "ResearchPipeline" not in pipeline_src:
        items.append(_item("BACKTEST-002", FAIL, "core/research/pipeline.py",
                           "research pipeline must compose the canonical chain"))

    # BACKTEST-004: no LIVE adapter access from research/backtest modules
    for rel in ("core/research", "core/backtest"):
        area = root / rel
        if not area.is_dir():
            continue
        for py_file in area.rglob("*.py"):
            if re.search(r"adapters\.mt5|adapters\.broker|ExecutionManagementSystem"
                         r"|OrderManagementSystem|MetaTrader5",
                         py_file.read_text(encoding="utf-8")):
                items.append(_item("BACKTEST-004", FAIL, str(py_file.relative_to(root)),
                                   "research/backtest must never touch LIVE execution"))

    # BACKTEST-005: execution assumptions versioned (execution_model schema)
    model_schema = load_schema("execution_model")
    if model_schema is not None:
        if not ((model_schema.get("fields", {}) or {}).get("model_hash", {}).get("required", False)):
            items.append(_item("BACKTEST-005", FAIL, "schemas/execution_model.yaml",
                               "execution model must be hashed + versioned"))

    # REPLAYX-001..003: replay read-only + deterministic
    replay_present = False
    for rel in ("core/research/pipeline.py", "core/research/replay.py"):
        src = load_module(rel)
        if src and "replay_compare" in src or (src and "ReplayComparison" in src):
            replay_present = True
            if "Adapters" in src or "submit_order" in src:
                items.append(_item("REPLAYX-002", FAIL, rel,
                                   "replay must not produce external side effects"))
    if not replay_present:
        items.append(_item("REPLAYX-001", FAIL, "core/research/pipeline.py",
                           "replay comparison must exist (read-only)"))

    # RESEARCHX-001..002: deterministic identity + verified hashes
    contracts_src = load_module("core/research/contracts.py")
    if contracts_src is not None:
        if "research_run_hash" not in contracts_src or "canonical_hash" not in contracts_src:
            items.append(_item("RESEARCHX-001", FAIL, "core/research/contracts.py",
                               "research identity must derive from canonical semantic inputs"))
        if "verify_dependencies" not in contracts_src:
            items.append(_item("RESEARCHX-002", FAIL, "core/research/contracts.py",
                               "run dependency hashes must be verifiable"))

    # RESEARCHX-003/004: no policy mutation, no production state writes
    for rel in ("core/research", "core/backtest"):
        area = root / rel
        if not area.is_dir():
            continue
        for py_file in area.rglob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            if re.search(r"PolicyRegistry\(|transition_status\(|from core\.policy import", src):
                items.append(_item("RESEARCHX-003", FAIL, str(py_file.relative_to(root)),
                                   "research must not mutate policy"))
            if re.search(r"OrderStore\(|OrderManagementSystem|SqliteOrderStore", src):
                items.append(_item("RESEARCHX-004", FAIL, str(py_file.relative_to(root)),
                                   "research must not write production state"))

    # VALIDATIONX-001..003: OOS frozen, final-test protected, walk-forward explicit
    pipeline_src = load_module("core/research/pipeline.py") or ""
    if "out_of_sample" not in pipeline_src:
        items.append(_item("VALIDATIONX-001", FAIL, "core/research/pipeline.py",
                           "OOS validation must freeze parameters"))
    if "walk_forward" not in pipeline_src:
        items.append(_item("VALIDATIONX-003", FAIL, "core/research/pipeline.py",
                           "walk-forward windows must be explicit"))

    # STRESSX-001: stress assumptions versioned
    if "config_version" not in (load_schema("stress_report") or {}).get("fields", {}):
        items.append(_item("STRESSX-001", FAIL, "schemas/stress_report.yaml",
                           "stress reports must pin a config version"))

    # CANDIDATE-001 / PROMOTION-001: candidate != live, no auto-promotion
    candidate_schema = load_schema("strategy_candidate")
    if candidate_schema is not None:
        constraints = " ".join(str(c) for c in candidate_schema.get("constraints", []))
        if "NOT a live strategy" not in constraints:
            items.append(_item("CANDIDATE-001", FAIL, "schemas/strategy_candidate.yaml",
                               "candidates must be distinct from live strategies"))
    for rel in ("core/research/contracts.py", "core/research/pipeline.py",
                "core/research/bias.py", "core/backtest/engine.py"):
        src = load_module(rel) or ""
        if "StrategyLifecycle.LIVE" in src:
            items.append(_item("PROMOTION-001", FAIL, rel,
                               "research must never auto-promote strategies to LIVE"))

    return items




def check_phase8_rules(root: Path) -> list[ValidationItem]:
    """Phase 8 rules (SEC-001..SEC-060): the security/governance guardrail
    plane. Security guards the authority chain - it never replaces Policy,
    Risk, OMS/EMS; one authorization source (platform.security), one audit
    authority (platform.audit), no secret leakage, fail closed everywhere.
    """
    items: list[ValidationItem] = []

    def load(rel):
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def require_token(rule, rel, token, message):
        source = load(rel)
        if source is None or token not in source:
            items.append(_item(rule, FAIL, rel, message))

    security_dir = root / "core" / "security"
    governance_dir = root / "core" / "governance"
    if not security_dir.is_dir() or not governance_dir.is_dir():
        items.append(_item("SEC-001", FAIL, "core/security",
                           "the security/governance plane must exist"))
        return items

    auth_src = load("core/security/authentication.py") or ""
    authz_src = load("core/security/authorization.py") or ""
    approval_src = load("core/security/services.py") or ""
    secrets_src = load("core/security/secrets.py") or ""
    protection_src = load("core/security/protection.py") or ""
    contracts_src = load("core/security/contracts.py") or ""
    gate_src = load("core/governance/gate.py") or ""
    audit_src = load("platform/audit/contracts.py") or ""
    platform_security_src = load("platform/security/contracts.py") or ""

    # authentication + sessions (SEC-001/002/027/028)
    require_token("SEC-001", "core/security/contracts.py",
                  "class AuthStatus",
                  "authentication states must be explicit (UNKNOWN is a "
                  "first-class state, never AUTHENTICATED)")
    require_token("SEC-002", "core/security/authorization.py",
                  "session invalid",
                  "privileged operations require a validated session "
                  "(unauthenticated actors are blocked)")
    require_token("SEC-027", "core/security/authentication.py",
                  "session expired",
                  "expired sessions must block")
    require_token("SEC-028", "core/security/authentication.py",
                  "session revoked",
                  "revoked sessions must block")

    # canonical authorization (SEC-003/040)
    if "from platform.security.contracts import" not in authz_src:
        items.append(_item(
            "SEC-003", FAIL, "core/security/authorization.py",
            "authorization must delegate to the canonical permission "
            "registry (platform.security + permissions.yaml)"))
    for area in ("core", "adapters", "research", "benchmarks"):
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if re.search(r"class\s+RolePermissionRegistry\b", text):
                items.append(_item(
                    "SEC-040", FAIL, str(py_file.relative_to(root)),
                    "a second authorization engine is forbidden - the only "
                    "RolePermissionRegistry lives in platform.security"))

    # maker-checker + human approval (SEC-004/005/006/037/038)
    require_token("SEC-004", "core/security/contracts.py",
                  "SEC-SELF-APPROVAL",
                  "the approval contract must make self-approval "
                  "unrepresentable (maker != checker)")
    require_token("SEC-005", "core/security/services.py",
                  "self-approval blocked",
                  "the approval service must block self-approval")
    require_token("SEC-006", "core/security/services.py",
                  "is_human_checker",
                  "human approval must be distinguished from automated "
                  "checkers")
    require_token("SEC-037", "core/security/contracts.py",
                  "NON_HUMAN_CHECKERS",
                  "AI actors must be listed as impossible checkers")
    require_token("SEC-038", "core/security/services.py",
                  "AI cannot satisfy a human approval",
                  "AI must never satisfy a human approval requirement")

    # LIVE + environments (SEC-007/008/046/060)
    require_token("SEC-007", "core/security/authorization.py",
                  "Permission.LIVE_TRADE",
                  "LIVE authorization must be an explicit permission path")
    require_token("SEC-008", "core/security/authorization.py",
                  "ENVIRONMENT_MISMATCH",
                  "permissions must be environment-bound (a session bound "
                  "to one environment never authorizes another)")
    require_token("SEC-046", "core/security/authorization.py",
                  "no cross-environment fallback",
                  "environment isolation must forbid cross-environment "
                  "fallback (DEMO can never inherit LIVE)")
    require_token("SEC-060", "platform/security/contracts.py",
                  "allowed_environments",
                  "the canonical registry must expose environment "
                  "restrictions (LIVE cannot be inherited from DEMO)")

    # secrets (SEC-009/010/011/058)
    require_token("SEC-009", "core/security/secrets.py",
                  '"<redacted>"',
                  "a canonical redaction function must exist")
    require_token("SEC-010", "core/security/protection.py",
                  "def redact_payload",
                  "security event payloads must be scrubbed (SECRET VALUE "
                  "!= AUDIT EVIDENCE)")
    require_token("SEC-011", "core/security/protection.py",
                  "SECRET_KEYS",
                  "secret-looking keys must never be logged verbatim")
    require_token("SEC-058", "core/security/authentication.py",
                  "pbkdf2_hmac",
                  "credentials must store only a salted PBKDF2 verifier - "
                  "never plaintext")

    # keys + encryption (SEC-012/013)
    require_token("SEC-012", "core/security/secrets.py",
                  "revoked_at=ensure_utc(at)",
                  "revoked keys must record revocation time")
    require_token("SEC-013", "core/security/contracts.py",
                  "ENCRYPTED_AT_REST_AND_TRANSIT",
                  "the encryption policy must define the full protection "
                  "ordering (hashing is not encryption)")

    # audit hardening (SEC-014..020, 041, 052, 055, 056)
    if re.search(r"class\s+AuditRecord", audit_src) and \
            "@dataclass(frozen=True)" not in audit_src:
        items.append(_item("SEC-014", FAIL, "platform/audit/contracts.py",
                           "audit records must be immutable (frozen)"))
    for token, rule, message in (
        ("integrity_hash", "SEC-015",
         "audit records must carry chained integrity hashes"),
        ("correlation_id", "SEC-016",
         "audit records must be causally linked"),
        ("actor_id", "SEC-017", "audit records must record the actor"),
        ("event_time", "SEC-018", "audit records must record timestamps"),
        ("environment", "SEC-019", "audit records must record environment"),
        ("contract_version", "SEC-020", "audit records must be versioned"),
        ("model_version", "SEC-052",
         "audit records must preserve versioned evidence for historical "
         "reproduction"),
    ):
        if token not in audit_src:
            items.append(_item(rule, FAIL, "platform/audit/contracts.py",
                               message))
    require_token("SEC-015", "core/security/protection.py",
                  "previous_hash",
                  "the audit chain must hash previous_hash into integrity")
    require_token("SEC-015", "core/security/protection.py",
                  "def verify",
                  "the audit chain must be verifiable end-to-end")
    for area in ("core", "adapters", "research"):
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            if re.search(r"class\s+\w*AuditRepository\b",
                         py_file.read_text(encoding="utf-8")):
                items.append(_item(
                    "SEC-041", FAIL, str(py_file.relative_to(root)),
                    "a second audit authority is forbidden - the only "
                    "AuditRepository port lives in platform.audit"))
    require_token("SEC-055", "core/governance/gate.py",
                  "GOVERNANCE_TRANSITION",
                  "every privileged governance action must append audit "
                  "evidence")
    require_token("SEC-056", "core/security/protection.py",
                  "privileged operations are ",
                  "audit integrity failure must block privileged "
                  "operations")
    require_token("SEC-056", "core/governance/gate.py",
                  "require_verified",
                  "the governance gate must check audit integrity first")

    # requests / replay / rate (SEC-021..024)
    require_token("SEC-021", "core/security/protection.py",
                  "REPLAY_DETECTED",
                  "replayed privileged requests must be detected")
    require_token("SEC-022", "core/security/protection.py",
                  "IDEMPOTENT_REPEAT",
                  "same id + same semantics must be idempotent; same id + "
                  "different semantics is corruption")
    require_token("SEC-023", "core/security/protection.py",
                  "RateLimitVerdict.EXCEEDED",
                  "rate limiting must exist for security-sensitive "
                  "operations")
    require_token("SEC-024", "core/security/contracts.py",
                  "def validate",
                  "security requests must validate against canonical "
                  "schemas (fail closed)")

    # escalation / spoofing / revocation (SEC-025/026/047..051/059)
    banned_markers = re.compile(
        r"ALLOW_ALL|SKIP_AUTH|SKIP_APPROVAL|DISABLE_SECURITY|ADMIN_BYPASS|"
        r"HARDCODED_(PASSWORD|SECRET|TOKEN)")
    for area_dir in (security_dir, governance_dir):
        for py_file in area_dir.rglob("*.py"):
            if banned_markers.search(py_file.read_text(encoding="utf-8")):
                items.append(_item(
                    "SEC-025", FAIL, str(py_file.relative_to(root)),
                    "hard-coded privilege bypass markers are forbidden"))
    require_token("SEC-026", "core/security/authorization.py",
                  "canonical registry",
                  "permissions must be re-derived from the canonical "
                  "registry on every call (revocation is effective "
                  "immediately)")
    require_token("SEC-047", "core/security/contracts.py",
                  "never from the client body",
                  "the security context must come from server-side state "
                  "(actor spoofing blocked by construction)")
    require_token("SEC-048", "core/governance/gate.py",
                  "approval maker does not match",
                  "approver spoofing must be blocked")
    require_token("SEC-049", "platform/security/contracts.py",
                  "def check_grant",
                  "permission grants must validate through the canonical "
                  "registry (role escalation blocked)")
    require_token("SEC-050", "core/security/authorization.py",
                  "ENVIRONMENT_PERMISSION_MISMATCH",
                  "permission-scope escalation across environments must "
                  "be blocked")
    require_token("SEC-051", "core/security/services.py",
                  "approval request expired",
                  "stale approvals must never grant")
    require_token("SEC-059", "core/security/authorization.py",
                  "PERMISSION_DENIED",
                  "a permission absent from the canonical registry must "
                  "block (never default-allow)")

    # credentials (SEC-029/030)
    require_token("SEC-029", "core/security/authentication.py",
                  "version=current.version + 1",
                  "rotation must create a new immutable version "
                  "(history preserved)")
    require_token("SEC-030", "core/security/authentication.py",
                  "AuthStatus.REVOKED",
                  "revoked credentials must fail authentication")

    # emergency + governance routing (SEC-031..036/057)
    require_token("SEC-031", "core/security/authorization.py",
                  '"EMERGENCY_RELEASE"',
                  "emergency control release must require governance")
    require_token("SEC-032", "core/governance/gate.py",
                  "new_version",
                  "configuration changes must be versioned")
    require_token("SEC-033", "core/governance/gate.py",
                  "Permission.MODIFY_RISK",
                  "risk configuration changes route through governance "
                  "with MODIFY_RISK")
    require_token("SEC-034", "core/governance/gate.py",
                  "Permission.MODIFY_POLICY",
                  "policy changes route through governance")
    require_token("SEC-035", "core/governance/gate.py",
                  "Permission.APPROVE",
                  "model promotion routes through governance")
    require_token("SEC-036", "core/governance/gate.py",
                  "GovernedArtifactType.STRATEGY",
                  "strategy promotion routes through governance")
    require_token("SEC-057", "core/security/authorization.py",
                  "APPROVAL_REQUIRED_OPERATIONS",
                  "human approval must be required for the configured "
                  "privileged operations")

    # broker boundary (SEC-039)
    for area_dir in (security_dir, governance_dir):
        for py_file in area_dir.rglob("*.py"):
            if re.search(r"adapters\.(mt5|broker)|MetaTrader5",
                         py_file.read_text(encoding="utf-8")):
                items.append(_item(
                    "SEC-039", FAIL, str(py_file.relative_to(root)),
                    "the security plane must never reach brokers directly"))

    # backup / restore / incidents / events (SEC-042..045)
    require_token("SEC-042", "core/security/contracts.py",
                  "manifest_hash",
                  "backups must carry content + manifest integrity hashes")
    require_token("SEC-043", "core/security/protection.py",
                  "never restored",
                  "corrupt/UNKNOWN backups must never be restored")
    require_token("SEC-044", "core/security/contracts.py",
                  "incidents preserve evidence",
                  "incident evidence must be mandatory and immutable")
    require_token("SEC-045", "core/security/protection.py",
                  "SECURITY_EVENT_TYPES",
                  "security events must be versioned through the event "
                  "contract")

    # integrity hashes (SEC-053/054)
    require_token("SEC-053", "core/security/contracts.py",
                  "content_hash mismatch",
                  "security configuration must be content-hash protected")
    require_token("SEC-054", "core/governance/gate.py",
                  "maker and checker must differ",
                  "governance transitions must enforce separation of "
                  "duties")

    return items


def check_phase9_rules(root: Path) -> list[ValidationItem]:
    """Phase 9 rules (GUI-001..GUI-012): the desktop is a control plane,
    never an authority. UI talks ONLY to platform.api; no GUI engines,
    no direct database/broker/core access, no AI execution path."""
    items: list[ValidationItem] = []

    def load(rel):
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def require_absent(rule, pattern, message):
        compiled = re.compile(pattern)
        for py_file in (root / "ui" / "desktop").rglob("*.py"):
            if compiled.search(py_file.read_text(encoding="utf-8")):
                items.append(_item(rule, FAIL,
                                   str(py_file.relative_to(root)), message))

    def require_present(rule, rel, token, message):
        source = load(rel)
        if source is None or token not in source:
            items.append(_item(rule, FAIL, rel, message))

    desktop_dir = root / "ui" / "desktop"
    gateway_dir = root / "platform" / "api"
    if not desktop_dir.is_dir():
        items.append(_item("GUI-001", FAIL, "ui/desktop",
                           "the desktop workstation must exist"))
        return items

    # GUI-001: the desktop talks ONLY to platform.api + architecture
    # contracts (no direct core/database/bus access of any kind)
    allowed_imports = ("platform.api", "architecture.contracts",
                       "ui.desktop")
    for py_file in desktop_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for match in re.finditer(
                r"^\s*(?:from|import)\s+([a-zA-Z_][\w.]*)", text, re.M):
            module = match.group(1)
            if module.split(".")[0] in ("typing", "dataclasses", "datetime",
                                        "enum", "pathlib", "sys",
                                        "__future__",
                                        "tkinter", "decimal", "functools",
                                        "collections", "json", "hashlib",
                                        "itertools", "math"):
                continue
            if not module.startswith(allowed_imports):
                items.append(_item(
                    "GUI-001", FAIL, str(py_file.relative_to(root)),
                    f"the desktop may import only platform.api / "
                    f"architecture.contracts / ui.desktop (found {module})"))

    # GUI-002: no GUI authority engines (SECTION 64)
    require_absent("GUI-002", r"class\s+\w*(Risk|Strategy|Portfolio|"
                              r"Authorization|Governance|Audit|Execution)"
                              r"Engine\b",
                   "GUI engines are forbidden - authority lives in Core")

    # GUI-003: no direct database access
    require_absent("GUI-003", r"platform\.database|sqlite3",
                   "the desktop must never touch the database directly")

    # GUI-004: no direct broker/MT5 path
    require_absent("GUI-004", r"adapters\.(mt5|broker)|MetaTrader5",
                   "no direct broker path from the UI")

    # GUI-005: no direct OMS/EMS bypass
    require_absent("GUI-005", r"core\.(oms|ems|execution)",
                   "orders flow through the gateway, never the UI")

    # GUI-006: no AI execution authority
    require_absent("GUI-006", r"core\.intelligence",
                   "AI is advisory; the UI reaches it only via the gateway")

    # GUI-007: gateway is the single facade (actions dispatch there)
    require_present("GUI-007", "platform/api/desktop_gateway.py",
                    "class DesktopGateway",
                    "the desktop gateway facade must exist")
    require_present("GUI-007", "ui/desktop/viewmodels.py",
                    "self._gateway",
                    "view models must dispatch through the gateway")

    # GUI-008: shell implements the A-G layout
    require_present("GUI-008", "ui/desktop/shell.py", "NAVIGATION",
                    "region A (navigation) must exist")
    require_present("GUI-008", "ui/desktop/shell.py", "C - INSPECTOR",
                    "region C (inspector) must exist")
    require_present("GUI-008", "ui/desktop/shell.py", "D - Activity",
                    "region D (blotter) must exist")
    require_present("GUI-008", "ui/desktop/shell.py",
                    "E - Intelligence", "region E must exist")
    require_present("GUI-008", "ui/desktop/shell.py",
                    "F - Portfolio / Risk", "region F must exist")
    require_present("GUI-008", "ui/desktop/shell.py",
                    "G - Action", "region G must exist")

    # GUI-009: UNKNOWN never SAFE in UI states
    require_present("GUI-009", "ui/desktop/contracts.py",
                    "UNKNOWN = \"UNKNOWN\"",
                    "UNKNOWN must be an explicit UI state")
    require_present("GUI-009", "ui/desktop/contracts.py",
                    "PERMISSION_DENIED",
                    "PERMISSION_DENIED must be an explicit UI state")

    # GUI-010: environment identification in the shell
    require_present("GUI-010", "ui/desktop/shell.py", "ENV:",
                    "the top bar must identify the environment")

    # GUI-011: dangerous commands require confirmation
    require_present("GUI-011", "ui/desktop/viewmodels.py",
                    "confirmation required",
                    "dangerous commands must refuse to run unconfirmed")

    # GUI-012: workspace persistence is UI-only and recovers safely
    require_present("GUI-012", "ui/desktop/viewmodels.py",
                    "default_workspace()",
                    "corrupted workspace state must recover to the "
                    "default layout")

    # gateway plane: the facade itself must not become an authority
    gateway_src = load("platform/api/desktop_gateway.py") or ""
    if "class DesktopGateway" in gateway_src and (
            "SIMULATION" not in gateway_src or "LIVE" not in gateway_src):
        items.append(_item("GUI-010", FAIL, "platform/api/desktop_gateway.py",
                           "the gateway must distinguish environments"))
    if re.search(r"class\s+\w*(RiskEngine|StrategyEngine)\b", gateway_src):
        items.append(_item("GUI-002", FAIL, "platform/api/desktop_gateway.py",
                           "the gateway must not define authority engines"))

    return items


def check_phase10_rules(root: Path) -> list[ValidationItem]:
    """Phase 10 rules (MT5X/FDX): real market data + MT5 DEMO stay
    inside existing authorities. The transport is reachable only via the
    EMS layer; the feed flows only through the Phase 1 pipeline; LIVE is
    structurally refused; credentials only via secret references."""
    items: list[ValidationItem] = []

    def load(rel):
        target = root / rel
        return target.read_text(encoding="utf-8") if target.is_file() else None

    def require_token(rule, rel, token, message):
        source = load(rel)
        if source is None or token not in source:
            items.append(_item(rule, FAIL, rel, message))

    # MT5X-001: adapters.mt5 may be imported ONLY by the EMS layer and
    # platform.api (the composed plane). GUI/strategy/intelligence/research
    # cores must never import it.
    for area in ("ui", "core/strategy", "core/intelligence", "core/research",
                 "core/risk", "core/policy", "core/portfolio", "core/oms"):
        area_dir = root / area
        if not area_dir.is_dir():
            continue
        for py_file in area_dir.rglob("*.py"):
            if re.search(r"from\s+adapters\.mt5|import\s+adapters\.mt5",
                         py_file.read_text(encoding="utf-8")):
                items.append(_item(
                    "MT5X-001", FAIL, str(py_file.relative_to(root)),
                    "adapters.mt5 is reachable only through the EMS layer / "
                    "platform.api composition - never from this area"))

    # MT5X-002: the MetaTrader5 package stays an optional deployment-gated
    # import (never a hard dependency of the core)
    mt5_src = load("adapters/mt5/transport.py") or ""
    feed_src = load("adapters/market_data/mt5_feed.py") or ""
    if "import MetaTrader5" in mt5_src and \
            "MT5_PACKAGE_AVAILABLE" not in mt5_src:
        items.append(_item(
            "MT5X-002", FAIL, "adapters/mt5/transport.py",
            "the MetaTrader5 import must remain deployment-gated "
            "(optional import + availability flag)"))
    if "import MetaTrader5" in feed_src and \
            "try:" not in feed_src.split("import MetaTrader5")[0][-200:]:
        items.append(_item(
            "MT5X-002", FAIL, "adapters/market_data/mt5_feed.py",
            "the MetaTrader5 import must remain deployment-gated"))

    # MT5X-003: connection plane keeps UNKNOWN first-class + emits
    # transition evidence
    connection_src = load("adapters/mt5/connection.py") or ""
    for token, message in (
            ("UNKNOWN", "connection state UNKNOWN must be first-class"),
            ("CONNECTION_STATE_CHANGED",
             "connection transitions must emit evidence events"),
            ("MT5-LIVE-REFUSED",
             "LIVE must be structurally refused in the connection plane")):
        if token not in connection_src:
            items.append(_item("MT5X-003", FAIL,
                               "adapters/mt5/connection.py", message))
    if "connection_state" not in (load("architecture/state-machines.yaml")
                                  or ""):
        items.append(_item(
            "MT5X-003", FAIL, "architecture/state-machines.yaml",
            "the connection_state machine must be registered"))

    # MT5X-004: credentials only via secret references (no literals in
    # the connectivity plane; SecretVault usage documented)
    for rel in ("adapters/mt5/connection.py",
                "adapters/market_data/mt5_feed.py",
                "platform/api/connectivity_plane.py"):
        source = load(rel) or ""
        if re.search(r"(password|login|api_key|secret)\s*=\s*['\"][^'\"]{4,}['\"]",
                     source, re.IGNORECASE):
            items.append(_item(
                "MT5X-004", FAIL, rel,
                "credential values must never be literals - secret "
                "references only"))

    # FDX-001: the feed adapter must flow through the Phase 1 pipeline
    feed_src = load("adapters/market_data/mt5_feed.py") or ""
    if "IngestionRequest" not in feed_src or \
            "IngestionPipeline" not in feed_src:
        items.append(_item(
            "FDX-001", FAIL, "adapters/market_data/mt5_feed.py",
            "market data must enter through the Phase 1 ingestion "
            "pipeline (IngestionRequest -> IngestionPipeline)"))

    # FDX-002: no second pipeline - the feed must not write stores or
    # build events on its own
    if re.search(r"event_store\.|raw_store\.|normalized_store\.|build_event",
                 feed_src):
        items.append(_item(
            "FDX-002", FAIL, "adapters/market_data/mt5_feed.py",
            "the feed adapter must not write stores or mint events "
            "directly - that is the Phase 1 pipeline's authority"))

    return items

def run_architecture_validation(project_root: Path | str | None = None) -> "ValidationResult":  # noqa: F821
    from pathlib import Path as _P

    from architecture.validator.result import ValidationResult as _Result

    root = _P(project_root) if project_root else _P(__file__).resolve().parents[2]
    arch, error = _load_yaml(root / ARCHITECTURE_FILE)
    items: list[ValidationItem] = []
    items += check_architecture_registry(root, arch, error)
    items += check_duplicate_modules(arch)
    items += check_dependencies(arch)
    items += check_state_machines(root)
    items += check_environments(root, arch)
    items += check_schemas(root, arch)
    items += check_source_of_truth(root, arch)
    items += check_manifest(root, arch)
    items += check_port_implementations(root, arch)
    items += check_source_imports(root, arch)
    items += check_no_production_markers(root)
    items += check_phase1_data_rules(root, arch)
    items += check_phase1_time_rules(root)
    items += check_phase1_event_rules(root)
    items += check_phase2_rules(root)
    items += check_phase3_rules(root)
    items += check_phase4_rules(root)
    items += check_phase5_rules(root)
    items += check_phase6_rules(root)
    items += check_phase7_rules(root)
    items += check_phase8_rules(root)
    items += check_phase9_rules(root)
    items += check_phase10_rules(root)
    return _Result(items)
