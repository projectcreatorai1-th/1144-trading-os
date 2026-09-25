"""Architecture Validator.

Validates the project against architecture/architecture.yaml (single source of
truth). Returns structured results - never a bare boolean (SECTION 20).

Usage (CLI): python -m architecture.validator
Usage (API): from architecture.validator import run_architecture_validation
"""
from architecture.validator.result import ValidationItem, ValidationResult, ValidationStatus
from architecture.validator.rules import run_architecture_validation

__all__ = [
    "ValidationItem",
    "ValidationResult",
    "ValidationStatus",
    "run_architecture_validation",
]
