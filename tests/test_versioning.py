"""Versioning and compatibility tests (SECTIONS 21-22) - including failure tests."""
from __future__ import annotations

import pytest

from architecture.contracts.errors import VersioningError
from architecture.contracts.versioning import (
    ChangeKind,
    Compatibility,
    SemVer,
    check_history,
    evaluate_changes,
    expected_bump_between,
)


class TestSemVer:
    def test_parse_and_str(self):
        assert str(SemVer.parse("2.1.3")) == "2.1.3"

    def test_ordering(self):
        assert SemVer.parse("1.9.9") < SemVer.parse("2.0.0")
        assert SemVer.parse("1.0.9") < SemVer.parse("1.1.0")
        assert SemVer.parse("1.0.0") == SemVer.parse("1.0.0")

    @pytest.mark.parametrize("bad", ["1.0", "1.0.0.0", "a.b.c", "1.0.x", "", 123, None])
    def test_invalid_versions_rejected(self, bad):
        with pytest.raises(VersioningError):
            SemVer.parse(bad)

    def test_negative_parts_rejected(self):
        with pytest.raises(VersioningError):
            SemVer(1, -1, 0)

    def test_semver_immutable(self):
        version = SemVer.parse("1.0.0")
        with pytest.raises(VersioningError):
            version.major = 2


class TestCompatibilityRules:
    def test_add_optional_field_is_compatible(self):
        verdict = evaluate_changes([{"kind": "ADDED_OPTIONAL_FIELD", "field": "x"}])
        assert verdict.overall is Compatibility.COMPATIBLE
        assert verdict.expected_bump.value == "MINOR"

    def test_add_required_field_is_breaking(self):
        verdict = evaluate_changes([{"kind": "ADDED_REQUIRED_FIELD", "field": "x"}])
        assert verdict.breaking
        assert verdict.expected_bump.value == "MAJOR"

    def test_remove_field_is_breaking(self):
        assert evaluate_changes([{"kind": "REMOVED_FIELD", "field": "x"}]).breaking

    def test_type_change_is_breaking(self):
        assert evaluate_changes([{"kind": "FIELD_TYPE_CHANGED", "field": "x"}]).breaking

    def test_enum_add_is_compatible_with_risk(self):
        verdict = evaluate_changes([{"kind": "ADDED_ENUM_VALUE", "enum": "e", "value": "V"}])
        assert verdict.overall is Compatibility.COMPATIBLE_WITH_RISK

    def test_enum_remove_is_breaking(self):
        assert evaluate_changes([{"kind": "REMOVED_ENUM_VALUE", "enum": "e", "value": "V"}]).breaking

    def test_semantic_change_is_breaking(self):
        assert evaluate_changes([{"kind": "SEMANTIC_CHANGE"}]).breaking

    def test_documentation_is_patch(self):
        verdict = evaluate_changes([{"kind": "DOCUMENTATION"}])
        assert verdict.overall is Compatibility.COMPATIBLE
        assert verdict.expected_bump.value == "PATCH"

    def test_worst_change_wins(self):
        verdict = evaluate_changes([
            {"kind": "DOCUMENTATION"},
            {"kind": "ADDED_OPTIONAL_FIELD", "field": "x"},
            {"kind": "REMOVED_FIELD", "field": "y"},
        ])
        assert verdict.breaking

    def test_unknown_change_kind_rejected(self):
        with pytest.raises(VersioningError):
            evaluate_changes([{"kind": "RENAMED_EVERYTHING"}])


class TestBumpRules:
    def test_major_bump(self):
        assert expected_bump_between("1.4.2", "2.0.0").value == "MAJOR"

    def test_minor_bump(self):
        assert expected_bump_between("1.4.2", "1.5.0").value == "MINOR"

    def test_patch_bump(self):
        assert expected_bump_between("1.4.2", "1.4.3").value == "PATCH"

    def test_version_regression_rejected(self):
        with pytest.raises(VersioningError):
            expected_bump_between("1.5.0", "1.4.9")

    def test_same_version_is_not_a_change(self):
        with pytest.raises(VersioningError):
            expected_bump_between("1.0.0", "1.0.0")


class TestHistoryChecks:
    def _history(self, changes, version="1.1.0", previous="1.0.0"):
        return [
            {"version": previous, "date": "2026-09-23", "summary": "initial", "changes": []},
            {"version": version, "date": "2026-09-24", "summary": "change", "changes": changes},
        ]

    def test_clean_history_has_no_issues(self):
        history = [
            {"version": "1.0.0", "date": "2026-09-23", "summary": "initial", "changes": []}
        ]
        assert check_history(history) == []

    def test_compatible_minor_change_ok(self):
        issues = check_history(self._history([{"kind": "ADDED_OPTIONAL_FIELD", "field": "x"}]))
        assert issues == []

    def test_breaking_change_with_minor_bump_fails(self):
        issues = check_history(self._history([{"kind": "REMOVED_FIELD", "field": "x"}], version="1.1.0"))
        assert any(i["rule_id"] == "SCHEMA-002" for i in issues)

    def test_breaking_change_with_major_bump_ok(self):
        issues = check_history(self._history([{"kind": "REMOVED_FIELD", "field": "x"}], version="2.0.0"))
        assert issues == []

    def test_silent_change_rejected(self):
        history = [
            {"version": "1.0.0", "changes": []},
            {"version": "1.0.1", "changes": []},
        ]
        issues = check_history(history)
        assert any(i["rule_id"] == "VER-004" for i in issues)

    def test_empty_history_rejected(self):
        assert any(i["rule_id"] == "VER-002" for i in check_history([]))

    def test_non_increasing_history_rejected(self):
        history = [
            {"version": "1.1.0", "changes": []},
            {"version": "1.0.0", "changes": [{"kind": "DOCUMENTATION"}]},
        ]
        assert any(i["rule_id"] == "VER-003" for i in check_history(history))

    def test_invalid_version_in_history_reported(self):
        history = [{"version": "one.two.three", "changes": []}]
        assert any(i["rule_id"] == "VER-001" for i in check_history(history))


class TestChangeKindContract:
    def test_all_change_kinds_declared(self):
        assert len(ChangeKind) == 8
