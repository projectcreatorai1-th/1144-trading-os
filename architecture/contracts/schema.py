"""Schema registry engine (SECTION 6).

Loads architecture/schema-registry.yaml + architecture/schemas/*.yaml and
provides deterministic, registry-driven instance validation:

- missing required field   -> SCHEMA-REQUIRED
- wrong field type         -> SCHEMA-TYPE
- invalid enum value       -> SCHEMA-ENUM
- invalid/naive timestamp  -> SCHEMA-TIMESTAMP
- unknown environment      -> SCHEMA-ENVIRONMENT (single source: architecture.yaml)

Validation never guesses: unknown schema ids are hard errors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID

import yaml

from architecture.contracts.errors import ContractError
from architecture.contracts.registry import find_project_root, load_registry

FIELD_TYPES = frozenset(
    {"identifier", "string", "semver", "timestamp", "decimal", "number", "integer", "boolean", "object", "array"}
)


@dataclass(frozen=True)
class ValidationIssue:
    rule_id: str
    severity: str
    location: str
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "location": self.location,
            "message": self.message,
            "details": self.details,
        }


class SchemaRegistryError(ContractError):
    rule_id = "SCHEMA-001"


@dataclass(frozen=True)
class SchemaDefinition:
    schema_id: str
    version: str
    status: str
    owner: str
    description: str
    python_binding: str | None
    fields: Mapping[str, Mapping[str, Any]]
    enums: Mapping[str, list[str]]
    constraints: tuple[str, ...]
    compatibility: str
    history: tuple[Mapping[str, Any], ...]


class SchemaRegistry:
    def __init__(self, index: Mapping[str, Mapping[str, Any]], schemas: Mapping[str, SchemaDefinition]) -> None:
        self._index = dict(index)
        self._schemas = dict(schemas)

    @property
    def schema_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._schemas))

    def get(self, schema_id: str) -> SchemaDefinition:
        schema = self._schemas.get(schema_id)
        if schema is None:
            raise SchemaRegistryError(
                f"Unknown schema id '{schema_id}'",
                location="schema-registry",
                details={"known_schemas": list(self.schema_ids)},
            )
        return schema

    def index_entry(self, schema_id: str) -> Mapping[str, Any]:
        return self._index[schema_id]

    def validate_instance(self, schema_id: str, data: Mapping[str, Any]) -> list[ValidationIssue]:
        """Registry-driven validation of a plain dict against a schema."""
        schema = self.get(schema_id)
        if not isinstance(data, Mapping):
            return [
                ValidationIssue(
                    rule_id="SCHEMA-TYPE",
                    severity="FAIL",
                    location=schema_id,
                    message=f"Instance for '{schema_id}' must be a mapping",
                    details={"actual_type": type(data).__name__},
                )
            ]
        issues: list[ValidationIssue] = []
        for name, spec in schema.fields.items():
            location = f"{schema_id}.{name}"
            required = bool(spec.get("required", False))
            value = data.get(name)
            present = name in data and value is not None
            if not present:
                if required:
                    issues.append(
                        ValidationIssue(
                            rule_id="SCHEMA-REQUIRED",
                            severity="FAIL",
                            location=location,
                            message=f"Missing required field '{name}'",
                            details={"schema_id": schema_id, "field": name},
                        )
                    )
                continue
            issues.extend(self._check_field(schema, name, spec, value, location))
        return issues

    def _check_field(
        self,
        schema: SchemaDefinition,
        name: str,
        spec: Mapping[str, Any],
        value: Any,
        location: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        ftype = spec.get("type", "string")
        if ftype not in FIELD_TYPES:
            issues.append(
                ValidationIssue(
                    rule_id="SCHEMA-004",
                    severity="FAIL",
                    location=location,
                    message=f"Schema declares unknown field type '{ftype}'",
                    details={"schema_id": schema.schema_id, "field": name},
                )
            )
            return issues

        ok, actual = _type_matches(ftype, value)
        if not ok:
            issues.append(
                ValidationIssue(
                    rule_id="SCHEMA-TYPE",
                    severity="FAIL",
                    location=location,
                    message=f"Field '{name}' expected type '{ftype}', got {actual}",
                    details={"schema_id": schema.schema_id, "field": name, "expected": ftype, "actual": actual},
                )
            )
            return issues

        if ftype == "timestamp" and not _is_valid_timestamp(value):
            issues.append(
                ValidationIssue(
                    rule_id="SCHEMA-TIMESTAMP",
                    severity="FAIL",
                    location=location,
                    message=f"Field '{name}' must be a timezone-aware timestamp (naive datetime rejected)",
                    details={"schema_id": schema.schema_id, "field": name},
                )
            )
            return issues

        enum_ref = spec.get("enum")
        if enum_ref is not None:
            values = schema.enums.get(enum_ref)
            if values is None:
                issues.append(
                    ValidationIssue(
                        rule_id="SCHEMA-004",
                        severity="FAIL",
                        location=location,
                        message=f"Field '{name}' references unknown enum '{enum_ref}'",
                        details={"schema_id": schema.schema_id, "field": name, "enum": enum_ref},
                    )
                )
            elif value not in values:
                issues.append(
                    ValidationIssue(
                        rule_id="SCHEMA-ENUM",
                        severity="FAIL",
                        location=location,
                        message=f"Field '{name}' value {value!r} not in enum '{enum_ref}'",
                        details={"schema_id": schema.schema_id, "field": name, "enum": enum_ref, "allowed": values},
                    )
                )
        elif name == "environment" and ftype == "string":
            supported = supported_environments()
            if value not in supported:
                issues.append(
                    ValidationIssue(
                        rule_id="SCHEMA-ENVIRONMENT",
                        severity="FAIL",
                        location=location,
                        message=f"Field '{name}' value {value!r} is not a supported environment",
                        details={"schema_id": schema.schema_id, "supported": supported},
                    )
                )
        return issues


def supported_environments() -> list[str]:
    return list(load_registry("architecture.yaml")["environments"]["supported"])


def _type_matches(ftype: str, value: Any) -> tuple[bool, str]:
    actual = type(value).__name__
    checks: dict[str, tuple[bool, ...]] = {
        "string": (isinstance(value, str),),
        "integer": (isinstance(value, int) and not isinstance(value, bool),),
        "number": (isinstance(value, (int, float)) and not isinstance(value, bool),),
        "boolean": (isinstance(value, bool),),
        "object": (isinstance(value, Mapping),),
        "array": (isinstance(value, (list, tuple)),),
        "decimal": (isinstance(value, (Decimal, int, str)) and not isinstance(value, bool),),
        "timestamp": (
            isinstance(value, datetime),
            isinstance(value, str),
        ),
        "semver": (isinstance(value, str),),
        "identifier": (isinstance(value, str),),
    }
    return (any(checks[ftype]), actual)


def _is_valid_timestamp(value: Any) -> bool:
    from architecture.contracts.time import ensure_utc

    if isinstance(value, datetime):
        try:
            ensure_utc(value)
            return True
        except ContractError:
            return False
    if isinstance(value, str):
        from architecture.contracts.time import parse_canonical

        try:
            parse_canonical(value)
            return True
        except ContractError:
            return False
    return False


@lru_cache(maxsize=None)
def build_schema_registry(project_root: str | None = None) -> SchemaRegistry:
    root = Path(project_root) if project_root else find_project_root()
    index_data = load_registry("schema-registry.yaml", project_root=str(root))
    index: dict[str, dict[str, Any]] = {}
    schemas: dict[str, SchemaDefinition] = {}
    for entry in index_data["schemas"]:
        schema_id = entry["schema_id"]
        if schema_id in index:
            raise SchemaRegistryError(
                f"Duplicate schema id '{schema_id}' in registry index",
                location="schema-registry",
                rule_id="ARCH-003",
            )
        path = root / "architecture" / entry["file"]
        if not path.is_file():
            raise SchemaRegistryError(
                f"Schema file missing: {entry['file']}",
                location="schema-registry",
                details={"schema_id": schema_id, "file": entry["file"]},
            )
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if data.get("schema_id") != schema_id:
            raise SchemaRegistryError(
                f"Schema file {entry['file']} declares schema_id {data.get('schema_id')!r}, "
                f"registry index expects {schema_id!r}",
                location="schema-registry",
                details={"schema_id": schema_id},
            )
        if data.get("version") != entry.get("version"):
            raise SchemaRegistryError(
                f"Version mismatch between registry index ({entry.get('version')}) and schema file "
                f"({data.get('version')}) for '{schema_id}'",
                location="schema-registry",
                details={"schema_id": schema_id},
            )
        index[schema_id] = dict(entry)
        schemas[schema_id] = SchemaDefinition(
            schema_id=schema_id,
            version=str(data["version"]),
            status=str(data["status"]),
            owner=str(data["owner"]),
            description=str(data.get("description", "")),
            python_binding=data.get("python_binding"),
            fields=data.get("fields", {}),
            enums=data.get("enums", {}) or {},
            constraints=tuple(data.get("constraints", []) or ()),
            compatibility=str(data.get("compatibility", "")),
            history=tuple(data.get("history", []) or ()),
        )
    return SchemaRegistry(index, schemas)
