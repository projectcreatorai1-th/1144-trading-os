"""Registry file loading.

Loads the YAML registries under architecture/. The architecture registry
files are the single source of truth for their domain (RULE 001); this module
only reads them, never interprets architecture rules.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REGISTRY_FILES = (
    "architecture.yaml",
    "schema-registry.yaml",
    "identifiers.yaml",
    "state-machines.yaml",
    "permissions.yaml",
    "api.yaml",
    "manifest.yaml",
    "currencies.yaml",
    "tolerances.yaml",
    "risk-config.yaml",
    "connectivity.yaml",
)


def find_project_root(start: Path | None = None) -> Path:
    """Locate the project root (directory containing architecture/ and core/)."""
    current = (start or Path(__file__).resolve()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "architecture" / "architecture.yaml").is_file() and (candidate / "core").is_dir():
            return candidate
    raise FileNotFoundError(
        "Project root not found: expected a directory containing architecture/architecture.yaml and core/"
    )


@lru_cache(maxsize=None)
def load_registry(filename: str, project_root: str | None = None) -> dict[str, Any]:
    """Load and cache a YAML registry by filename (e.g. 'architecture.yaml')."""
    if filename not in REGISTRY_FILES:
        raise ValueError(f"Unknown registry file: {filename}. Known: {REGISTRY_FILES}")
    root = Path(project_root) if project_root else find_project_root()
    path = root / "architecture" / filename
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Registry {filename} must be a YAML mapping")
    return data
