"""Tests for diff normalization and comparison."""

from __future__ import annotations

from datetime import UTC, date, datetime

from repo_planner.diff import (
    diff_issue,
    diff_milestone,
    normalize_state,
    normalize_timestamp,
    summarize_text_change,
)
from repo_planner.models import IssuePayload, MilestonePayload
from support import make_issue, make_milestone


def issue_payload(**overrides: object) -> IssuePayload:
    base: dict[str, object] = {
        "title": "Issue",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [],
        "milestone_number": None,
    }
    base.update(overrides)
    return IssuePayload.model_validate(base)


def test_no_changes_when_equivalent() -> None:
    desired = issue_payload(
        body="line one\r\nline two\r\n",
        labels=["Bug", "docs"],
    )
    remote = make_issue(1, body="line one\nline two", labels=["docs", "bug"])
    assert diff_issue(desired, remote) == []


def test_meaningful_markdown_whitespace_preserved() -> None:
    desired = issue_payload(body="a  b\n\nc")
    remote = make_issue(1, body="a b\n\nc")
    changes = diff_issue(desired, remote)
    assert [change.field for change in changes] == ["body"]


def test_blank_optional_values_equal_empty() -> None:
    desired = issue_payload(labels=[], assignees=[])
    remote = make_issue(1, labels=[], assignees=[])
    assert diff_issue(desired, remote) == []


def test_title_state_and_milestone_changes() -> None:
    desired = issue_payload(title="New", state="closed", milestone_number=5)
    remote = make_issue(1, title="Old", state="open", milestone_number=4, milestone_title="M4")
    fields = {change.field for change in diff_issue(desired, remote)}
    assert fields == {"title", "state", "milestone"}


def test_milestone_cleared_when_desired_none() -> None:
    desired = issue_payload()
    remote = make_issue(1, milestone_number=3, milestone_title="M3")
    changes = diff_issue(desired, remote)
    assert [change.field for change in changes] == ["milestone"]
    assert changes[0].desired is None


def test_milestone_compared_by_title_when_number_unknown() -> None:
    desired = issue_payload(milestone_number=None)
    remote = make_issue(1, milestone_number=3, milestone_title="Milestone One")
    assert diff_issue(desired, remote, desired_milestone_title="Milestone One") == []
    changes = diff_issue(desired, remote, desired_milestone_title="Other")
    assert [change.field for change in changes] == ["milestone"]


def test_due_on_change_and_clear() -> None:
    desired = MilestonePayload(title="M", due_on=None)
    remote = make_milestone(1, title="M", due_on=date(2026, 1, 1))
    changes = diff_milestone(desired, remote)
    assert [change.field for change in changes] == ["due_on"]
    assert changes[0].desired is None


def test_description_normalization() -> None:
    desired = MilestonePayload(title="M", description="hello\r\n\r\n")
    remote = make_milestone(1, title="M", description="hello")
    assert diff_milestone(desired, remote) == []


def test_summarize_text_change() -> None:
    assert summarize_text_change("a\nb\nc", "a\nc") == "1 added lines"
    assert summarize_text_change("a\nc", "a\nb\nc") == "1 removed lines"
    assert summarize_text_change("a\nX\nc", "a\nY\nc") == "1 changed lines"
    assert summarize_text_change("same", "same") == "text differs"


def test_normalize_state() -> None:
    assert normalize_state("opened") == "open"
    assert normalize_state("active") == "open"
    assert normalize_state("open") == "open"
    assert normalize_state("closed") == "closed"
    assert normalize_state(None) == "closed"


def test_normalize_timestamp() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert normalize_timestamp(naive) == normalize_timestamp(aware)
    assert normalize_timestamp(aware).microsecond == 0
    assert normalize_timestamp(None) is None
