"""Shared test helpers: project scaffolding, fake provider and mock HTTP API."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from repo_planner.config import Project, load_project
from repo_planner.http import HttpClient
from repo_planner.models import (
    IssuePayload,
    MilestonePayload,
    RemoteIssue,
    RemoteMilestone,
)
from repo_planner.providers.base import Provider
from repo_planner.providers.github import GitHubProvider
from repo_planner.providers.gitlab import GitLabProvider

FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

DEFAULT_MANIFEST: dict[str, Any] = {
    "version": 1,
    "milestones": [
        {
            "key": "m1",
            "title": "Milestone One",
            "description_file": "docs/m1.md",
            "due_on": "2026-06-01",
        },
    ],
    "issues": [
        {
            "key": "i1",
            "title": "First issue",
            "body_file": "docs/i1.md",
            "labels": ["bug"],
            "milestone": "m1",
        },
        {
            "key": "i2",
            "title": "Second issue",
            "body_file": "docs/i2.md",
            "depends_on": ["i1"],
        },
    ],
}

DEFAULT_DOCS: dict[str, str] = {
    "docs/m1.md": "# {{ milestone.title }}\n\nWelcome.\n",
    "docs/i1.md": "Body for {{ issue.title }}.\n",
    "docs/i2.md": (
        "Depends on {{ issues['i1'].title }}"
        "{% if issues['i1'].url %} ({{ issues['i1'].url }}){% endif %}.\n"
    ),
}


def make_project(
    root: Path,
    *,
    provider: str = "github",
    repository: str = "owner/repo",
    manifest: dict[str, Any] | None = None,
    docs: dict[str, str] | None = None,
    config_extra: dict[str, Any] | None = None,
) -> Project:
    """Write a complete project skeleton and return the loaded project."""

    (root / "configs").mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {"provider": provider, "repository": repository}
    config.update(config_extra or {})
    (root / "configs" / "repo-planner.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "configs" / "manifest.json").write_text(
        json.dumps(manifest if manifest is not None else DEFAULT_MANIFEST), encoding="utf-8"
    )
    for relative, text in (docs if docs is not None else DEFAULT_DOCS).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return load_project(root)


def make_issue(
    number: int,
    *,
    title: str = "Issue",
    body: str = "",
    labels: Sequence[str] = (),
    assignees: Sequence[str] = (),
    state: str = "open",
    milestone_number: int | None = None,
    milestone_title: str | None = None,
    updated_at: datetime | None = None,
    remote_id: int | None = None,
) -> RemoteIssue:
    """Build a RemoteIssue for tests."""

    return RemoteIssue(
        id=remote_id if remote_id is not None else 10000 + number,
        number=number,
        title=title,
        body=body,
        state=state,
        labels=list(labels),
        assignees=list(assignees),
        milestone_number=milestone_number,
        milestone_title=milestone_title,
        updated_at=updated_at or FIXED_NOW,
        url=f"https://fake/issues/{number}",
    )


def make_milestone(
    number: int,
    *,
    title: str = "Milestone",
    description: str = "",
    state: str = "open",
    due_on: Any = None,
    updated_at: datetime | None = None,
    remote_id: int | None = None,
) -> RemoteMilestone:
    """Build a RemoteMilestone for tests."""

    return RemoteMilestone(
        id=remote_id if remote_id is not None else 20000 + number,
        number=number,
        title=title,
        description=description,
        state=state,
        due_on=due_on,
        updated_at=updated_at or FIXED_NOW,
        url=f"https://fake/milestones/{number}",
    )


class FakeProvider(Provider):
    """In-memory provider used to exercise the reconciler without HTTP."""

    name = "fake"

    def __init__(
        self,
        *,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        milestones: list[RemoteMilestone] | None = None,
        issues: list[RemoteIssue] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__("owner/repo", "token")
        self._labels = list(labels or [])
        self._assignees = None if assignees is None else list(assignees)
        self._milestone_data: dict[int, RemoteMilestone] = {
            milestone.id: milestone for milestone in milestones or []
        }
        self._issue_data: dict[int, RemoteIssue] = {issue.id: issue for issue in issues or []}
        self._clock = clock or (lambda: FIXED_NOW)
        self._next_id = (
            max(
                [milestone.id for milestone in self._milestone_data.values()]
                + [issue.id for issue in self._issue_data.values()]
                + [0]
            )
            + 1
        )
        self.mutations: list[str] = []
        self.created_labels: list[str] = []

    # ------------------------------------------------------------------ hooks

    def fetch_issues(self) -> list[RemoteIssue]:
        return [issue.model_copy(deep=True) for issue in self._issue_data.values()]

    def fetch_milestones(self) -> list[RemoteMilestone]:
        return [milestone.model_copy(deep=True) for milestone in self._milestone_data.values()]

    def fetch_labels(self) -> list[str]:
        return list(self._labels)

    def check_assignees(self, usernames: Sequence[str]) -> list[str]:
        if self._assignees is None:
            return []
        known = {name.casefold() for name in self._assignees}
        return [name for name in usernames if name.casefold() not in known]

    def _alloc_id(self) -> int:
        identifier = self._next_id
        self._next_id += 1
        return identifier

    def _milestone_title(self, number: int | None) -> str | None:
        if number is None:
            return None
        for milestone in self._milestone_data.values():
            if milestone.number == number:
                return milestone.title
        return None

    def _create_issue(self, payload: IssuePayload) -> RemoteIssue:
        number = max([issue.number for issue in self._issue_data.values()] + [0]) + 1
        issue = RemoteIssue(
            id=self._alloc_id(),
            number=number,
            title=payload.title,
            body=payload.body,
            state=payload.state,
            labels=list(payload.labels),
            assignees=list(payload.assignees),
            milestone_number=payload.milestone_number,
            milestone_title=self._milestone_title(payload.milestone_number),
            updated_at=self._clock(),
            url=f"https://fake/issues/{number}",
        )
        self._issue_data[issue.id] = issue
        self.mutations.append(f"create issue #{number}")
        return issue.model_copy(deep=True)

    def _update_issue(self, number: int, payload: IssuePayload) -> RemoteIssue:
        for issue in self._issue_data.values():
            if issue.number != number:
                continue
            updated = issue.model_copy(
                update={
                    "title": payload.title,
                    "body": payload.body,
                    "state": payload.state,
                    "labels": list(payload.labels),
                    "assignees": list(payload.assignees),
                    "milestone_number": payload.milestone_number,
                    "milestone_title": self._milestone_title(payload.milestone_number),
                    "updated_at": self._clock(),
                }
            )
            self._issue_data[updated.id] = updated
            self.mutations.append(f"update issue #{number}")
            return updated.model_copy(deep=True)
        raise AssertionError(f"unknown issue #{number}")

    def _create_milestone(self, payload: MilestonePayload) -> RemoteMilestone:
        number = max([item.number for item in self._milestone_data.values()] + [0]) + 1
        milestone = RemoteMilestone(
            id=self._alloc_id(),
            number=number,
            title=payload.title,
            description=payload.description,
            state=payload.state,
            due_on=payload.due_on,
            updated_at=self._clock(),
            url=f"https://fake/milestones/{number}",
        )
        self._milestone_data[milestone.id] = milestone
        self.mutations.append(f"create milestone #{number}")
        return milestone.model_copy(deep=True)

    def _update_milestone(self, number: int, payload: MilestonePayload) -> RemoteMilestone:
        for milestone in self._milestone_data.values():
            if milestone.number != number:
                continue
            updated = milestone.model_copy(
                update={
                    "title": payload.title,
                    "description": payload.description,
                    "state": payload.state,
                    "due_on": payload.due_on,
                    "updated_at": self._clock(),
                }
            )
            self._milestone_data[updated.id] = updated
            self.mutations.append(f"update milestone #{number}")
            return updated.model_copy(deep=True)
        raise AssertionError(f"unknown milestone #{number}")

    def _create_label(self, name: str) -> None:
        if name not in self._labels:
            self._labels.append(name)
        self.created_labels.append(name)
        self.mutations.append(f"create label {name!r}")

    # ------------------------------------------------------------ test helpers

    def issue_by_number(self, number: int) -> RemoteIssue:
        for issue in self._issue_data.values():
            if issue.number == number:
                return issue
        raise KeyError(number)

    def milestone_by_number(self, number: int) -> RemoteMilestone:
        for milestone in self._milestone_data.values():
            if milestone.number == number:
                return milestone
        raise KeyError(number)

    def mutate_issue(self, number: int, **updates: Any) -> None:
        issue = self.issue_by_number(number)
        self._issue_data[issue.id] = issue.model_copy(update=updates)


class MockAPI:
    """Tiny router for ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self._routes: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []
        self.requests: list[httpx.Request] = []

    def add(
        self,
        method: str,
        path: str,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        self._routes.append((method.upper(), path, handler))

    def json(
        self,
        method: str,
        path: str,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.add(
            method,
            path,
            lambda request: httpx.Response(status_code, json=payload, headers=headers or {}),
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, path, handler in self._routes:
            if request.method == method and self._path_matches(request, path):
                return handler(request)
        return httpx.Response(
            404, json={"message": f"no mock route for {request.method} {request.url.path}"}
        )

    @staticmethod
    def _path_matches(request: httpx.Request, path: str) -> bool:
        decoded = unquote(request.url.path)
        candidates = {request.url.path, decoded}
        # GitLab requests are made under the /api/v4 base URL; accept routes
        # registered without that prefix for readability.
        if decoded.startswith("/api/v4"):
            candidates.add(decoded[len("/api/v4") :])
        return path in candidates

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def bodies(self, method: str, path: str) -> list[Any]:
        """Return JSON-decoded request bodies matching method+path."""

        results: list[Any] = []
        for request in self.requests:
            if request.method == method.upper() and self._path_matches(request, path):
                results.append(json.loads(request.content) if request.content else None)
        return results


def github_provider(
    transport: httpx.BaseTransport,
    *,
    repository: str = "owner/repo",
    token: str = "test-token",
) -> GitHubProvider:
    """Build a GitHubProvider wired to a mock transport."""

    client = HttpClient(
        "https://api.github.com",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "repo-planner"},
        transport=transport,
        sleep=lambda _seconds: None,
    )
    return GitHubProvider(repository, token, client=client)


def gitlab_provider(
    transport: httpx.BaseTransport,
    *,
    repository: str = "group/repo",
    token: str = "test-token",
) -> GitLabProvider:
    """Build a GitLabProvider wired to a mock transport."""

    client = HttpClient(
        "https://gitlab.com/api/v4",
        headers={"PRIVATE-TOKEN": token, "User-Agent": "repo-planner"},
        transport=transport,
        sleep=lambda _seconds: None,
    )
    return GitLabProvider(repository, token, client=client)
