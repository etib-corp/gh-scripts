"""GitHub provider adapter (REST API v3-compatible)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote

from ..errors import ProviderError
from ..http import HttpClient, raise_for_status
from ..models import IssuePayload, MilestonePayload, RemoteIssue, RemoteMilestone
from .base import Provider

DEFAULT_API_URL = "https://api.github.com"
USER_AGENT = "repo-planner"


def _parse_state(value: str | None) -> Literal["open", "closed"]:
    return "closed" if value == "closed" else "open"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None


class GitHubProvider(Provider):
    """GitHub adapter using REST endpoints under ``/repos/{owner}/{repo}``."""

    name = "github"

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        client: HttpClient | None = None,
        api_url: str | None = None,
    ) -> None:
        if client is None:
            client = HttpClient(
                api_url or DEFAULT_API_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": USER_AGENT,
                },
            )
        super().__init__(repository, token, client=client)

    @property
    def client(self) -> HttpClient:
        assert self._client is not None, "GitHubProvider requires an HTTP client"
        return self._client

    @property
    def _repo_path(self) -> str:
        owner, name = self.repository.split("/", 1)
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    # ------------------------------------------------------------------ fetch

    def fetch_issues(self) -> list[RemoteIssue]:
        items = self._paginate(f"{self._repo_path}/issues", {"state": "all"})
        return [self._parse_issue(item) for item in items if "pull_request" not in item]

    def fetch_milestones(self) -> list[RemoteMilestone]:
        items = self._paginate(f"{self._repo_path}/milestones", {"state": "all"})
        return [self._parse_milestone(item) for item in items]

    def fetch_labels(self) -> list[str]:
        items = self._paginate(f"{self._repo_path}/labels")
        return [str(item.get("name", "")) for item in items if item.get("name")]

    def check_assignees(self, usernames: Sequence[str]) -> list[str]:
        usernames = list(usernames)
        if not usernames:
            return []
        candidates = {
            str(item.get("login", "")).casefold()
            for item in self._paginate(f"{self._repo_path}/assignees")
        }
        return [name for name in usernames if name.casefold() not in candidates]

    # ---------------------------------------------------------------- mutations

    def _create_issue(self, payload: IssuePayload) -> RemoteIssue:
        body: dict[str, Any] = {
            "title": payload.title,
            "body": payload.body,
            "labels": list(payload.labels),
            "assignees": list(payload.assignees),
        }
        if payload.milestone_number is not None:
            body["milestone"] = payload.milestone_number
        response = self.client.request("POST", f"{self._repo_path}/issues", json=body)
        raise_for_status(response, context=f"create issue {payload.title!r}")
        issue = self._parse_issue(response.json())
        if payload.state == "closed":
            issue = self._update_issue(issue.number, payload)
        return issue

    def _update_issue(self, number: int, payload: IssuePayload) -> RemoteIssue:
        body: dict[str, Any] = {
            "title": payload.title,
            "body": payload.body,
            "labels": list(payload.labels),
            "assignees": list(payload.assignees),
            "state": payload.state,
            "milestone": payload.milestone_number,
        }
        response = self.client.request("PATCH", f"{self._repo_path}/issues/{number}", json=body)
        raise_for_status(response, context=f"update issue #{number}")
        return self._parse_issue(response.json())

    def _create_milestone(self, payload: MilestonePayload) -> RemoteMilestone:
        body: dict[str, Any] = {"title": payload.title, "description": payload.description}
        if payload.state == "closed":
            body["state"] = "closed"
        if payload.due_on is not None:
            body["due_on"] = f"{payload.due_on.isoformat()}T00:00:00Z"
        response = self.client.request("POST", f"{self._repo_path}/milestones", json=body)
        raise_for_status(response, context=f"create milestone {payload.title!r}")
        return self._parse_milestone(response.json())

    def _update_milestone(self, number: int, payload: MilestonePayload) -> RemoteMilestone:
        body: dict[str, Any] = {
            "title": payload.title,
            "description": payload.description,
            "state": payload.state,
            "due_on": f"{payload.due_on.isoformat()}T00:00:00Z" if payload.due_on else None,
        }
        response = self.client.request("PATCH", f"{self._repo_path}/milestones/{number}", json=body)
        raise_for_status(response, context=f"update milestone #{number}")
        return self._parse_milestone(response.json())

    def _create_label(self, name: str) -> None:
        response = self.client.request("POST", f"{self._repo_path}/labels", json={"name": name})
        raise_for_status(response, context=f"create label {name!r}")

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_issue(data: dict[str, Any]) -> RemoteIssue:
        milestone = data.get("milestone") or {}
        labels = [
            str(label.get("name", ""))
            for label in data.get("labels") or []
            if isinstance(label, dict)
        ]
        assignees = [
            str(user.get("login", ""))
            for user in data.get("assignees") or []
            if isinstance(user, dict)
        ]
        return RemoteIssue(
            id=int(data["id"]),
            number=int(data["number"]),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            state=_parse_state(data.get("state")),
            labels=[name for name in labels if name],
            assignees=[name for name in assignees if name],
            milestone_number=int(milestone["number"]) if milestone.get("number") else None,
            milestone_title=milestone.get("title"),
            updated_at=data.get("updated_at"),
            url=data.get("html_url"),
        )

    @staticmethod
    def _parse_milestone(data: dict[str, Any]) -> RemoteMilestone:
        return RemoteMilestone(
            id=int(data["id"]),
            number=int(data["number"]),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            state=_parse_state(data.get("state")),
            due_on=_parse_date(data.get("due_on")),
            updated_at=data.get("updated_at"),
            url=data.get("html_url"),
        )

    # --------------------------------------------------------------- pagination

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        while url is not None:
            response = self.client.request("GET", url, params=query)
            raise_for_status(response, context=f"GET {path}")
            data = response.json()
            if not isinstance(data, list):
                raise ProviderError(f"GET {path}: expected a JSON array from the GitHub API")
            results.extend(item for item in data if isinstance(item, dict))
            next_link = response.links.get("next")
            url = next_link.get("url") if next_link else None
            query = None
        return results
