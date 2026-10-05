"""Normalization and diffing between desired and remote resources."""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from .models import IssuePayload, MilestonePayload, RemoteIssue, RemoteMilestone


@dataclass(frozen=True)
class FieldChange:
    """A single field whose desired value differs from the remote value."""

    field: str
    desired: Any
    remote: Any


def normalize_text(value: str | None) -> str:
    """Normalize line endings and trailing whitespace for comparison.

    Meaningful Markdown whitespace (indentation, in-line spacing, hard line
    breaks) is preserved; only the document edges and line endings change.
    """

    if not value:
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_labels(values: Iterable[str] | None) -> tuple[str, ...]:
    """Case-insensitive, order-insensitive label normalization."""

    if not values:
        return ()
    return tuple(sorted({value.strip().casefold() for value in values if value and value.strip()}))


def normalize_state(value: str | None) -> str:
    """Normalize provider-specific state names to ``open``/``closed``."""

    cleaned = (value or "").strip().casefold()
    return "open" if cleaned in {"open", "opened", "active"} else "closed"


def normalize_timestamp(value: datetime | None) -> datetime | None:
    """Normalize a timestamp to UTC with second precision."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def summarize_text_change(desired: str, remote: str) -> str:
    """Return a compact line-based summary of a text change."""

    remote_lines = remote.splitlines()
    desired_lines = desired.splitlines()
    matcher = difflib.SequenceMatcher(a=remote_lines, b=desired_lines)
    added = removed = changed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added += j2 - j1
        elif tag == "delete":
            removed += i2 - i1
        elif tag == "replace":
            overlap = min(i2 - i1, j2 - j1)
            changed += overlap
            if i2 - i1 > overlap:
                removed += (i2 - i1) - overlap
            elif j2 - j1 > overlap:
                added += (j2 - j1) - overlap
    parts: list[str] = []
    if changed:
        parts.append(f"{changed} changed")
    if added:
        parts.append(f"{added} added")
    if removed:
        parts.append(f"{removed} removed")
    if not parts:
        return "text differs"
    return ", ".join(parts) + " lines"


def diff_milestone(desired: MilestonePayload, remote: RemoteMilestone) -> list[FieldChange]:
    """Compare a desired milestone against its remote counterpart."""

    changes: list[FieldChange] = []
    if desired.title != remote.title:
        changes.append(FieldChange("title", desired.title, remote.title))
    if normalize_text(desired.description) != normalize_text(remote.description):
        changes.append(FieldChange("description", desired.description, remote.description))
    if normalize_state(desired.state) != normalize_state(remote.state):
        changes.append(FieldChange("state", desired.state, remote.state))
    if (desired.due_on or None) != (remote.due_on or None):
        changes.append(FieldChange("due_on", desired.due_on, remote.due_on))
    return changes


def diff_issue(
    desired: IssuePayload,
    remote: RemoteIssue,
    *,
    desired_milestone_title: str | None = None,
) -> list[FieldChange]:
    """Compare a desired issue against its remote counterpart.

    ``desired_milestone_title`` is the manifest title of the milestone the
    issue references. It is used whenever the desired milestone has no resolved
    remote number yet (for example because it will be created in the same run).
    """

    changes: list[FieldChange] = []
    if desired.title != remote.title:
        changes.append(FieldChange("title", desired.title, remote.title))
    if normalize_text(desired.body) != normalize_text(remote.body):
        changes.append(FieldChange("body", desired.body, remote.body))
    if normalize_labels(desired.labels) != normalize_labels(remote.labels):
        changes.append(FieldChange("labels", list(desired.labels), list(remote.labels)))
    if normalize_labels(desired.assignees) != normalize_labels(remote.assignees):
        changes.append(FieldChange("assignees", list(desired.assignees), list(remote.assignees)))
    if normalize_state(desired.state) != normalize_state(remote.state):
        changes.append(FieldChange("state", desired.state, remote.state))
    if _milestone_changed(desired, remote, desired_milestone_title):
        desired_display: str | date | None = desired_milestone_title
        changes.append(FieldChange("milestone", desired_display, remote.milestone_title))
    return changes


def _milestone_changed(
    desired: IssuePayload,
    remote: RemoteIssue,
    desired_milestone_title: str | None,
) -> bool:
    desired_number = desired.milestone_number
    remote_number = remote.milestone_number
    if desired_number is None and desired_milestone_title is None:
        return remote_number is not None or remote.milestone_title is not None
    if remote_number is None and remote.milestone_title is None:
        return True
    if desired_number is not None and remote_number is not None:
        return desired_number != remote_number
    return desired_milestone_title != remote.milestone_title


def serialize_value(value: Any) -> Any:
    """Convert values to JSON-friendly primitives for reporting."""

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Sequence) and not isinstance(value, str):
        return list(value)
    return value
