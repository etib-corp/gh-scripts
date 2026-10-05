"""The provider-agnostic interface shared by every adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from ..http import HttpClient
from ..markers import has_marker
from ..models import IssuePayload, MilestonePayload, RemoteIssue, RemoteMilestone


class Provider(ABC):
    """Remote repository operations needed by repo-planner.

    The public methods implement caching and delegate the provider-specific
    HTTP work to the ``fetch_*``/``_create_*``/``_update_*`` hooks.
    """

    name: ClassVar[str] = "provider"

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        client: HttpClient | None = None,
    ) -> None:
        self.repository = repository
        self._token = token
        self._client = client
        self._issues_cache: list[RemoteIssue] | None = None
        self._milestones_cache: list[RemoteMilestone] | None = None

    # ------------------------------------------------------------------ reads

    def list_issues(self) -> list[RemoteIssue]:
        """List every issue in the repository (cached per run)."""

        if self._issues_cache is None:
            self._issues_cache = self.fetch_issues()
        return list(self._issues_cache)

    def list_milestones(self) -> list[RemoteMilestone]:
        """List every milestone in the repository (cached per run)."""

        if self._milestones_cache is None:
            self._milestones_cache = self.fetch_milestones()
        return list(self._milestones_cache)

    def list_labels(self) -> list[str]:
        """List every label defined in the repository."""

        return self.fetch_labels()

    def check_assignees(self, usernames: Sequence[str]) -> list[str]:
        """Return the usernames that cannot be assigned.

        Providers that cannot enumerate valid assignees return an empty list
        (the adapter validates assignees when building payloads instead).
        """

        return []

    def get_issue(self, remote_id: int) -> RemoteIssue | None:
        """Return a remote issue by its provider ID, if it exists."""

        return next((issue for issue in self.list_issues() if issue.id == remote_id), None)

    def get_milestone(self, remote_id: int) -> RemoteMilestone | None:
        """Return a remote milestone by its provider ID, if it exists."""

        return next(
            (milestone for milestone in self.list_milestones() if milestone.id == remote_id),
            None,
        )

    def find_issues_by_marker(self, key: str) -> list[RemoteIssue]:
        """Return remote issues whose body carries the identity marker for ``key``."""

        return [issue for issue in self.list_issues() if has_marker(issue.body, "issue", key)]

    def find_milestones_by_marker(self, key: str) -> list[RemoteMilestone]:
        """Return remote milestones whose description carries the marker for ``key``."""

        return [
            milestone
            for milestone in self.list_milestones()
            if has_marker(milestone.description, "milestone", key)
        ]

    # -------------------------------------------------------------- mutations

    def create_issue(self, payload: IssuePayload) -> RemoteIssue:
        """Create an issue from a provider-agnostic payload."""

        issue = self._create_issue(payload)
        self._upsert_issue(issue)
        return issue

    def update_issue(self, number: int, payload: IssuePayload) -> RemoteIssue:
        """Update an existing issue to match a provider-agnostic payload."""

        issue = self._update_issue(number, payload)
        self._upsert_issue(issue)
        return issue

    def create_milestone(self, payload: MilestonePayload) -> RemoteMilestone:
        """Create a milestone from a provider-agnostic payload."""

        milestone = self._create_milestone(payload)
        self._upsert_milestone(milestone)
        return milestone

    def update_milestone(self, number: int, payload: MilestonePayload) -> RemoteMilestone:
        """Update an existing milestone to match a provider-agnostic payload."""

        milestone = self._update_milestone(number, payload)
        self._upsert_milestone(milestone)
        return milestone

    def create_label(self, name: str) -> None:
        """Create a label (used by ``--create-missing-labels``)."""

        self._create_label(name)

    # ------------------------------------------------------------- subclass API

    @abstractmethod
    def fetch_issues(self) -> list[RemoteIssue]:
        """Fetch every issue from the provider."""

    @abstractmethod
    def fetch_milestones(self) -> list[RemoteMilestone]:
        """Fetch every milestone from the provider."""

    @abstractmethod
    def fetch_labels(self) -> list[str]:
        """Fetch every label name from the provider."""

    @abstractmethod
    def _create_issue(self, payload: IssuePayload) -> RemoteIssue:
        """Provider-specific issue creation."""

    @abstractmethod
    def _update_issue(self, number: int, payload: IssuePayload) -> RemoteIssue:
        """Provider-specific issue update."""

    @abstractmethod
    def _create_milestone(self, payload: MilestonePayload) -> RemoteMilestone:
        """Provider-specific milestone creation."""

    @abstractmethod
    def _update_milestone(self, number: int, payload: MilestonePayload) -> RemoteMilestone:
        """Provider-specific milestone update."""

    @abstractmethod
    def _create_label(self, name: str) -> None:
        """Provider-specific label creation."""

    # ------------------------------------------------------------------ internals

    def _upsert_issue(self, issue: RemoteIssue) -> None:
        if self._issues_cache is None:
            return
        for index, existing in enumerate(self._issues_cache):
            if existing.id == issue.id:
                self._issues_cache[index] = issue
                return
        self._issues_cache.append(issue)

    def _upsert_milestone(self, milestone: RemoteMilestone) -> None:
        if self._milestones_cache is None:
            return
        for index, existing in enumerate(self._milestones_cache):
            if existing.id == milestone.id:
                self._milestones_cache[index] = milestone
                return
        self._milestones_cache.append(milestone)
