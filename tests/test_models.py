"""Tests for manifest model validation."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from repo_planner.models import IssueSpec, Manifest


def test_valid_manifest_defaults() -> None:
    manifest = Manifest.model_validate(
        {
            "milestones": [{"key": "m1", "title": "Milestone One"}],
            "issues": [{"key": "i1", "title": "Issue One", "body_file": "docs/i1.md"}],
        }
    )
    assert manifest.version == 1
    milestone = manifest.milestones[0]
    assert milestone.state == "open"
    assert milestone.due_on is None
    assert milestone.metadata == {}
    issue = manifest.issues[0]
    assert issue.labels == []
    assert issue.depends_on == []
    assert issue.milestone is None


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate({"milestones": [], "issues": [], "unknown": True})


def test_duplicate_milestone_keys() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Manifest.model_validate(
            {
                "milestones": [
                    {"key": "m1", "title": "A"},
                    {"key": "m1", "title": "B"},
                ]
            }
        )
    assert "duplicate milestone key" in str(excinfo.value)


def test_unknown_milestone_reference() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Manifest.model_validate(
            {
                "milestones": [],
                "issues": [
                    {"key": "i1", "title": "I", "body_file": "docs/i1.md", "milestone": "nope"}
                ],
            }
        )
    assert "unknown milestone key" in str(excinfo.value)


def test_unknown_dependency() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Manifest.model_validate(
            {
                "issues": [
                    {"key": "i1", "title": "I", "body_file": "docs/i1.md", "depends_on": ["ghost"]}
                ]
            }
        )
    assert "unknown dependency key" in str(excinfo.value)


def test_self_dependency_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Manifest.model_validate(
            {
                "issues": [
                    {"key": "i1", "title": "I", "body_file": "docs/i1.md", "depends_on": ["i1"]}
                ]
            }
        )
    assert "depends on itself" in str(excinfo.value)


def test_dependency_cycle_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Manifest.model_validate(
            {
                "issues": [
                    {"key": "a", "title": "A", "body_file": "docs/a.md", "depends_on": ["b"]},
                    {"key": "b", "title": "B", "body_file": "docs/b.md", "depends_on": ["a"]},
                ]
            }
        )
    message = str(excinfo.value)
    assert "cycle" in message
    assert "a" in message and "b" in message


def test_invalid_key_pattern() -> None:
    for bad_key in ["bad key", "<script>", "-leading", ""]:
        with pytest.raises(ValidationError):
            IssueSpec.model_validate({"key": bad_key, "title": "T", "body_file": "docs/i.md"})


def test_labels_cleaned_and_deduplicated() -> None:
    issue = IssueSpec.model_validate(
        {
            "key": "i1",
            "title": "T",
            "body_file": "docs/i1.md",
            "labels": [" bug ", "BUG", "", "docs"],
            "assignees": [" alice "],
        }
    )
    assert issue.labels == ["bug", "docs"]
    assert issue.assignees == ["alice"]


def test_state_validation() -> None:
    with pytest.raises(ValidationError):
        IssueSpec.model_validate(
            {"key": "i1", "title": "T", "body_file": "docs/i1.md", "state": "merged"}
        )


def test_blank_title_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueSpec.model_validate({"key": "i1", "title": "   ", "body_file": "docs/i1.md"})


def test_description_file_blank_becomes_none() -> None:
    manifest = Manifest.model_validate(
        {"milestones": [{"key": "m1", "title": "M", "description_file": "  "}]}
    )
    assert manifest.milestones[0].description_file is None


def test_due_on_parsed_as_date() -> None:
    manifest = Manifest.model_validate(
        {"milestones": [{"key": "m1", "title": "M", "due_on": "2026-06-01"}]}
    )
    assert manifest.milestones[0].due_on == date(2026, 6, 1)
