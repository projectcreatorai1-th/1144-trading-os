"""Contract versioning and compatibility (SECTIONS 21-22).

MAJOR.MINOR.PATCH. Breaking changes must raise MAJOR; backward-compatible
additions raise MINOR; corrections raise PATCH. Silent contract changes are
forbidden: every change is described in the schema's history.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from architecture.contracts.errors import VersioningError


class SemVer:
    """Immutable semantic version with total ordering."""

    __slots__ = ("major", "minor", "patch")

    def __init__(self, major: int, minor: int, patch: int) -> None:
        for part in (major, minor, patch):
            if not isinstance(part, int) or isinstance(part, bool) or part < 0:
                raise VersioningError(
                    f"Version parts must be non-negative integers, got {(major, minor, patch)}",
                    location="semver",
                )
        object.__setattr__(self, "major", major)
        object.__setattr__(self, "minor", minor)
        object.__setattr__(self, "patch", patch)

    def __setattr__(self, *_args: object) -> None:
        raise VersioningError("SemVer is immutable", location="semver")

    @classmethod
    def parse(cls, value: object, *, location: str = "version") -> "SemVer":
        if not isinstance(value, str):
            raise VersioningError(
                f"Version must be a 'MAJOR.MINOR.PATCH' string, got {value!r}",
                location=location,
            )
        parts = value.split(".")
        if len(parts) != 3:
            raise VersioningError(
                f"Invalid version '{value}': expected MAJOR.MINOR.PATCH",
                location=location,
                details={"value": value},
            )
        try:
            nums = tuple(int(p) for p in parts)
        except ValueError as exc:
            raise VersioningError(
                f"Invalid version '{value}': non-numeric part",
                location=location,
                details={"value": value},
            ) from exc
        return cls(*nums)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __repr__(self) -> str:
        return f"SemVer({str(self)})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SemVer) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __lt__(self, other: "SemVer") -> bool:
        return self._key() < other._key()

    def __le__(self, other: "SemVer") -> bool:
        return self._key() <= other._key()

    def _key(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def bump(self) -> "VersionBump":
        if self.patch:
            return VersionBump.PATCH
        if self.minor:
            return VersionBump.MINOR
        return VersionBump.MAJOR


class VersionBump(Enum):
    MAJOR = "MAJOR"
    MINOR = "MINOR"
    PATCH = "PATCH"


class Compatibility(Enum):
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RISK = "COMPATIBLE_WITH_RISK"
    BREAKING = "BREAKING"


class ChangeKind(Enum):
    """Kinds of contract changes and their compatibility impact (SECTION 22)."""

    ADDED_OPTIONAL_FIELD = ("ADDED_OPTIONAL_FIELD", VersionBump.MINOR, Compatibility.COMPATIBLE)
    ADDED_REQUIRED_FIELD = ("ADDED_REQUIRED_FIELD", VersionBump.MAJOR, Compatibility.BREAKING)
    REMOVED_FIELD = ("REMOVED_FIELD", VersionBump.MAJOR, Compatibility.BREAKING)
    FIELD_TYPE_CHANGED = ("FIELD_TYPE_CHANGED", VersionBump.MAJOR, Compatibility.BREAKING)
    ADDED_ENUM_VALUE = ("ADDED_ENUM_VALUE", VersionBump.MINOR, Compatibility.COMPATIBLE_WITH_RISK)
    REMOVED_ENUM_VALUE = ("REMOVED_ENUM_VALUE", VersionBump.MAJOR, Compatibility.BREAKING)
    SEMANTIC_CHANGE = ("SEMANTIC_CHANGE", VersionBump.MAJOR, Compatibility.BREAKING)
    DOCUMENTATION = ("DOCUMENTATION", VersionBump.PATCH, Compatibility.COMPATIBLE)

    def __init__(self, label: str, bump: VersionBump, compatibility: Compatibility) -> None:
        self.label = label
        self.expected_bump = bump
        self.compatibility = compatibility


@dataclass(frozen=True)
class CompatibilityVerdict:
    overall: Compatibility
    expected_bump: VersionBump
    change_kinds: tuple[ChangeKind, ...]

    @property
    def breaking(self) -> bool:
        return self.overall is Compatibility.BREAKING


def _change_kind_by_label(label: str) -> ChangeKind:
    for kind in ChangeKind:
        if kind.label == label:
            return kind
    raise VersioningError(
        f"Unknown change kind {label!r}",
        location="history.changes",
        details={"known_kinds": [k.label for k in ChangeKind]},
    )


def evaluate_changes(changes: list[dict[str, str]]) -> CompatibilityVerdict:
    """Evaluate a change list (history entry) into a compatibility verdict."""
    kinds: list[ChangeKind] = []
    for change in changes:
        label = change.get("kind") if isinstance(change, dict) else None
        if label is None and isinstance(change, str):
            label = change
        if not isinstance(label, str):
            raise VersioningError(
                f"Malformed change entry {change!r}",
                location="history.changes",
            )
        kinds.append(_change_kind_by_label(label))

    severity_order = [Compatibility.COMPATIBLE, Compatibility.COMPATIBLE_WITH_RISK, Compatibility.BREAKING]
    bump_order = [VersionBump.PATCH, VersionBump.MINOR, VersionBump.MAJOR]
    overall = Compatibility.COMPATIBLE
    bump = VersionBump.PATCH
    for kind in kinds:
        if severity_order.index(kind.compatibility) > severity_order.index(overall):
            overall = kind.compatibility
        if bump_order.index(kind.expected_bump) > bump_order.index(bump):
            bump = kind.expected_bump
    return CompatibilityVerdict(overall=overall, expected_bump=bump, change_kinds=tuple(kinds))


def expected_bump_between(previous: str, current: str) -> VersionBump:
    """The minimum version bump implied by moving previous -> current."""
    prev = SemVer.parse(previous)
    cur = SemVer.parse(current)
    if cur == prev:
        raise VersioningError(
            "Contract change without version bump is forbidden (RULE 015)",
            location="history",
            details={"version": current},
        )
    if cur < prev:
        raise VersioningError(
            f"Version regression forbidden: {previous} -> {current}",
            location="history",
            details={"previous": previous, "current": current},
        )
    if cur.major > prev.major:
        return VersionBump.MAJOR
    if cur.minor > prev.minor:
        return VersionBump.MINOR
    return VersionBump.PATCH


def check_history(history: list[dict[str, object]]) -> list[dict[str, object]]:
    """Validate a schema history: ordered versions, no silent changes,
    declared bumps consistent with the declared changes."""
    if not history:
        return [
            {
                "rule_id": "VER-002",
                "severity": "FAIL",
                "location": "history",
                "message": "Contract history must not be empty",
                "details": {},
            }
        ]
    issues: list[dict[str, object]] = []
    versions: list[SemVer] = []
    for index, entry in enumerate(history):
        version = entry.get("version")
        try:
            parsed = SemVer.parse(version, location=f"history[{index}]")
        except VersioningError as exc:
            issues.append(exc.to_dict())
            continue
        if versions and parsed <= versions[-1]:
            issues.append(
                {
                    "rule_id": "VER-003",
                    "severity": "FAIL",
                    "location": f"history[{index}]",
                    "message": f"History versions must strictly increase (got {parsed} after {versions[-1]})",
                    "details": {"version": str(parsed)},
                }
            )
        versions.append(parsed)
        changes = entry.get("changes", [])
        if index > 0:
            if not changes:
                issues.append(
                    {
                        "rule_id": "VER-004",
                        "severity": "FAIL",
                        "location": f"history[{index}]",
                        "message": "Every contract change must be declared (silent changes forbidden, RULE 015)",
                        "details": {"version": str(parsed)},
                    }
                )
                continue
            try:
                verdict = evaluate_changes(list(changes))
                bump = expected_bump_between(str(versions[index - 1]), str(parsed))
                bump_rank = {VersionBump.PATCH: 0, VersionBump.MINOR: 1, VersionBump.MAJOR: 2}
                if bump_rank[bump] < bump_rank[verdict.expected_bump]:
                    issues.append(
                        {
                            "rule_id": "SCHEMA-002",
                            "severity": "FAIL",
                            "location": f"history[{index}]",
                            "message": (
                                f"Breaking change declared in {parsed} but version only bumped {bump.value}"
                            ),
                            "details": {
                                "version": str(parsed),
                                "declared_bump": bump.value,
                                "required_bump": verdict.expected_bump.value,
                                "changes": [k.label for k in verdict.change_kinds],
                            },
                        }
                    )
            except VersioningError as exc:
                issues.append(exc.to_dict())
    return issues
