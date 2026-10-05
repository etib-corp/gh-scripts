"""Pydantic models for manifests, remote resources, payloads, state and caches."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .dependencies import topological_order
from .errors import DependencyCycleError

KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

Key = Annotated[str, Field(min_length=1, max_length=64, pattern=KEY_PATTERN)]


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return [value for value in dict.fromkeys(values) if counts[value] > 1]


def _clean_name(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be empty")
    return cleaned


def _clean_name_list(value: Any) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected a list of strings, got {type(value).__name__}")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"expected a list of strings, got a {type(item).__name__}")
        cleaned = item.strip()
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        result.append(cleaned)
    return result


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MilestoneSpec(_StrictModel):
    """A milestone declared in the local manifest (the desired state)."""

    key: Key
    title: str = Field(min_length=1)
    description_file: str | None = None
    due_on: date | None = None
    state: Literal["open", "closed"] = "open"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", mode="before")
    @classmethod
    def _clean_title(cls, value: Any) -> Any:
        return _clean_name(value)

    @field_validator("description_file", mode="before")
    @classmethod
    def _clean_description_file(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"expected a string, got {type(value).__name__}")
        cleaned = value.strip()
        return cleaned or None


class IssueSpec(_StrictModel):
    """An issue declared in the local manifest (the desired state)."""

    key: Key
    title: str = Field(min_length=1)
    body_file: str = Field(min_length=1)
    labels: list[str] = Field(default_factory=list)
    milestone: str | None = None
    assignees: list[str] = Field(default_factory=list)
    state: Literal["open", "closed"] = "open"
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "body_file", mode="before")
    @classmethod
    def _clean_required(cls, value: Any) -> Any:
        return _clean_name(value)

    @field_validator("milestone", mode="before")
    @classmethod
    def _clean_milestone(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"expected a string, got {type(value).__name__}")
        cleaned = value.strip()
        return cleaned or None

    @field_validator("labels", "assignees", "depends_on", mode="before")
    @classmethod
    def _clean_lists(cls, value: Any) -> Any:
        return _clean_name_list(value)


class Manifest(_StrictModel):
    """Top-level manifest: the desired state of the repository."""

    version: int = 1
    milestones: list[MilestoneSpec] = Field(default_factory=list)
    issues: list[IssueSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self) -> Manifest:
        problems: list[str] = []

        duplicate_milestones = _duplicates([spec.key for spec in self.milestones])
        if duplicate_milestones:
            problems.append(f"duplicate milestone key(s): {', '.join(duplicate_milestones)}")
        duplicate_issues = _duplicates([spec.key for spec in self.issues])
        if duplicate_issues:
            problems.append(f"duplicate issue key(s): {', '.join(duplicate_issues)}")

        milestone_keys = {spec.key for spec in self.milestones}
        issue_keys = {spec.key for spec in self.issues}
        for issue in self.issues:
            if issue.milestone is not None and issue.milestone not in milestone_keys:
                problems.append(f"issue {issue.key!r}: unknown milestone key {issue.milestone!r}")
            for dependency in issue.depends_on:
                if dependency == issue.key:
                    problems.append(f"issue {issue.key!r}: depends on itself")
                elif dependency not in issue_keys:
                    problems.append(f"issue {issue.key!r}: unknown dependency key {dependency!r}")

        if not problems:
            graph = {issue.key: list(issue.depends_on) for issue in self.issues}
            try:
                topological_order(graph)
            except DependencyCycleError as exc:
                problems.append(str(exc))

        if problems:
            raise ValueError("; ".join(problems))
        return self

    def milestone_by_key(self, key: str) -> MilestoneSpec | None:
        """Return the milestone with ``key``, if declared."""

        return next((spec for spec in self.milestones if spec.key == key), None)

    def issue_by_key(self, key: str) -> IssueSpec | None:
        """Return the issue with ``key``, if declared."""

        return next((spec for spec in self.issues if spec.key == key), None)


class RemoteMilestone(_StrictModel):
    """A milestone as it exists on the remote provider."""

    id: int
    number: int
    title: str
    description: str = ""
    state: Literal["open", "closed"] = "open"
    due_on: date | None = None
    updated_at: datetime | None = None
    url: str | None = None


class RemoteIssue(_StrictModel):
    """An issue as it exists on the remote provider."""

    id: int
    number: int
    title: str
    body: str = ""
    state: Literal["open", "closed"] = "open"
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    milestone_number: int | None = None
    milestone_title: str | None = None
    updated_at: datetime | None = None
    url: str | None = None


class MilestonePayload(_StrictModel):
    """Provider-agnostic desired fields for a milestone."""

    title: str
    description: str = ""
    state: Literal["open", "closed"] = "open"
    due_on: date | None = None


class IssuePayload(_StrictModel):
    """Provider-agnostic desired fields for an issue."""

    title: str
    body: str = ""
    state: Literal["open", "closed"] = "open"
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    milestone_number: int | None = None


class ResourceState(_StrictModel):
    """Local mapping of a manifest key to a remote resource."""

    id: int
    number: int
    updated_at: datetime | None = None
    url: str | None = None


class ProjectState(_StrictModel):
    """Contents of ``configs/state.json``."""

    version: int = 1
    provider: str | None = None
    repository: str | None = None
    milestones: dict[str, ResourceState] = Field(default_factory=dict)
    issues: dict[str, ResourceState] = Field(default_factory=dict)

    def bucket(self, kind: str) -> dict[str, ResourceState]:
        """Return the mapping for ``kind`` (``"milestone"`` or ``"issue"``)."""

        return self.milestones if kind == "milestone" else self.issues


class MilestoneCache(_StrictModel):
    """Contents of the remote milestone cache written by ``sync milestones``."""

    version: int = 1
    fetched_at: datetime
    provider: str
    repository: str
    milestones: list[RemoteMilestone] = Field(default_factory=list)
    managed: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "KEY_PATTERN",
    "IssuePayload",
    "IssueSpec",
    "Key",
    "Manifest",
    "MilestoneCache",
    "MilestonePayload",
    "MilestoneSpec",
    "ProjectState",
    "RemoteIssue",
    "RemoteMilestone",
    "ResourceState",
    "ValidationError",
]
