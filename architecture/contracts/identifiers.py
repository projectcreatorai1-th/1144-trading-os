"""Global identifier standard (SECTION 7).

The single source of truth for id kinds, prefixes and formats is
architecture/identifiers.yaml. No module may invent its own id format.
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from architecture.contracts.errors import IdentifierValidationError
from architecture.contracts.registry import load_registry

KIND_EVENT_ID = "event_id"
KIND_CAUSATION_ID = "causation_id"


@lru_cache(maxsize=None)
def _identifier_table() -> dict[str, dict[str, Any]]:
    registry = load_registry("identifiers.yaml")
    return {entry["kind"]: entry for entry in registry["identifiers"]}


@lru_cache(maxsize=None)
def _standard() -> dict[str, Any]:
    return load_registry("identifiers.yaml")["standard"]


def identifier_kinds() -> tuple[str, ...]:
    return tuple(sorted(_identifier_table()))


def prefix_for(kind: str) -> str:
    entry = _identifier_table().get(kind)
    if entry is None:
        raise IdentifierValidationError(
            f"Unknown identifier kind '{kind}'",
            location="identifiers",
            details={"known_kinds": identifier_kinds()},
        )
    return entry["prefix"]


def new_identifier(kind: str) -> str:
    """Generate a new canonical identifier: {prefix}_{uuid4hex}."""
    return f"{prefix_for(kind)}_{uuid.uuid4().hex}"


def is_valid_identifier(kind: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    entry = _identifier_table().get(kind)
    if entry is None:
        return False
    prefix = entry["prefix"]
    suffix = value[len(prefix) + 1:] if value.startswith(prefix + "_") else ""
    return len(suffix) == 32 and all(c in "0123456789abcdef" for c in suffix)


def validate_identifier(kind: str, value: Any, *, location: str = "") -> str:
    """Validate an identifier against the standard; raise on violation."""
    if not isinstance(value, str) or not value:
        raise IdentifierValidationError(
            f"Identifier for kind '{kind}' must be a non-empty string",
            location=location or kind,
            details={"kind": kind, "value_type": type(value).__name__},
        )
    entry = _identifier_table().get(kind)
    if entry is None:
        raise IdentifierValidationError(
            f"Unknown identifier kind '{kind}'",
            location=location or kind,
            details={"known_kinds": identifier_kinds()},
        )
    if not is_valid_identifier(kind, value):
        raise IdentifierValidationError(
            f"Value '{value}' does not match identifier standard for kind '{kind}' "
            f"(expected format '{_standard()['format']}' with prefix '{entry['prefix']}')",
            location=location or kind,
            details={"kind": kind, "expected_prefix": entry["prefix"], "value": value},
        )
    return value


def is_valid_any_identifier(value: Any) -> bool:
    """True when value matches the canonical format for ANY known kind (for correlation_id)."""
    if not isinstance(value, str):
        return False
    for kind in identifier_kinds():
        if is_valid_identifier(kind, value):
            return True
    return False


def validate_any_identifier(value: Any, *, location: str = "correlation_id") -> str:
    if not is_valid_any_identifier(value):
        raise IdentifierValidationError(
            "correlation_id must be a valid canonical identifier of some registered kind",
            location=location,
            details={"value_type": type(value).__name__},
        )
    return value
