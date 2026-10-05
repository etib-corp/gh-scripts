"""GitLab provider adapter (REST API v4)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal
from urllib.parse import quote

from ..errors import ManifestValidationError, ProviderError
from ..http import HttpClient, raise_for_status
from ..models import IssuePayload, MilestonePayload, RemoteIssue, RemoteMilestone
from .base import Provider

DEFAULT_API_URL = "https://gitlab.com/api/v4"
USER_AGENT = "repo-planner"


def _parse_state(value: str | None) -> Literal["open", "closed"]:
    return "open" if value in {"opened", "active"} else "closed"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class GitLabProvider(Provider):
    """GitLab adapter scoped to a single project (namespaces supported)."""

    name = "gitlab"

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
                headers={"PRIVATE-TOKEN": token, "User-Agent": USER_AGENT},
            )
        super().__init__(repository, token, client=client)
        self._user_ids: dict[str, int] = {}

    @property
    def client(self) -> HttpClient:
        assert self._client is not None, "GitLabProvider requires an HTTP client"
        return self._client

    @property
    def _project_path(self) -> str:
        return f"/projects/{quote(self.repository, safe='')}"

    # ------------------------------------------------------------------ fetch

    def fetch_issues(self) -> list[RemoteIssue]:
        # Query both states explicitly so closed resources are never missed.
        items = self._paginate(f"{self._project_path}/issues", {"state": "opened"})
        items += self._paginate(f"{self._project_path}/issues", {"state": "closed"})
        return [self._parse_issue(item) for item in items]

    def fetch_milestones(self) -> list[RemoteMilestone]:
        items = self._paginate(f"{self._project_path}/milestones", {"state": "active"})
        items += self._paginate(f"{self._project_path}/milestones", {"state": "closed"})
        return [self._parse_milestone(item) for item in items]

    def fetch_labels(self) -> list[str]:
        items = self._paginate(f"{self._project_path}/labels")
        return [str(item.get("name", "")) for item in items if item.get("name")]

    def check_assignees(self, usernames: Sequence[str]) -> list[str]:
        missing: list[str] = []
        for name in usernames:
            if self._find_user_id(name) is None:
                missing.append(name)
        return missing

    # ---------------------------------------------------------------- mutations

    def _issue_body(self, payload: IssuePayload) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": payload.title,
            "description": payload.body,
            "labels": ",".join(payload.labels),
            "assignee_ids": [self._user_id(name) for name in payload.assignees],
        }
        if payload.milestone_number is not None:
            body["milestone_id"] = payload.milestone_number
        return body

    def _create_issue(self, payload: IssuePayload) -> RemoteIssue:
        response = self.client.request(
            "POST", f"{self._project_path}/issues", json=self._issue_body(payload)
        )
        raise_for_status(response, context=f"create issue {payload.title!r}")
        issue = self._parse_issue(response.json())
        if payload.state == "closed":
            issue = self._update_issue(issue.number, payload)
        return issue

    def _update_issue(self, number: int, payload: IssuePayload) -> RemoteIssue:
        body = self._issue_body(payload)
        # An explicit milestone_id of 0 clears the milestone on GitLab.
        body["milestone_id"] = (
            payload.milestone_number if payload.milestone_number is not None else 0
        )
        body["state_event"] = "close" if payload.state == "closed" else "reopen"
        response = self.client.request("PUT", f"{self._project_path}/issues/{number}", json=body)
        raise_for_status(response, context=f"update issue #{number}")
        return self._parse_issue(response.json())

    def _create_milestone(self, payload: MilestonePayload) -> RemoteMilestone:
        body: dict[str, Any] = {"title": payload.title, "description": payload.description}
        if payload.due_on is not None:
            body["due_date"] = payload.due_on.isoformat()
        response = self.client.request("POST", f"{self._project_path}/milestones", json=body)
        raise_for_status(response, context=f"create milestone {payload.title!r}")
        milestone = self._parse_milestone(response.json())
        if payload.state == "closed":
            milestone = self._update_milestone(milestone.number, payload)
        return milestone

    def _update_milestone(self, number: int, payload: MilestonePayload) -> RemoteMilestone:
        body: dict[str, Any] = {
            "title": payload.title,
            "description": payload.description,
            # An empty string removes the due date on GitLab.
            "due_date": payload.due_on.isoformat() if payload.due_on else "",
            "state_event": "close" if payload.state == "closed" else "activate",
        }
        response = self.client.request(
            "PUT", f"{self._project_path}/milestones/{number}", json=body
        )
        raise_for_status(response, context=f"update milestone #{number}")
        return self._parse_milestone(response.json())

    def _create_label(self, name: str) -> None:
        response = self.client.request(
            "POST", f"{self._project_path}/labels", json={"name": name, "color": "#428BCA"}
        )
        raise_for_status(response, context=f"create label {name!r}")

    # ------------------------------------------------------------------ users

    def _find_user_id(self, username: str) -> int | None:
        cached = self._user_ids.get(username.casefold())
        if cached is not None:
            return cached
        users = self._paginate("/users", {"username": username})
        for user in users:
            if str(user.get("username", "")).casefold() == username.casefold():
                identifier = int(user["id"])
                self._user_ids[username.casefold()] = identifier
                return identifier
        return None

    def _user_id(self, username: str) -> int:
        identifier = self._find_user_id(username)
        if identifier is None:
            raise ManifestValidationError(f"assignee {username!r} was not found on GitLab")
        return identifier

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_issue(data: dict[str, Any]) -> RemoteIssue:
        milestone = data.get("milestone") or {}
        raw_labels = data.get("labels") or []
        labels: list[str] = []
        for label in raw_labels:
            name = str(label.get("name", "")) if isinstance(label, dict) else str(label)
            if name:
                labels.append(name)
        assignees = [
            str(user.get("username", ""))
            for user in data.get("assignees") or []
            if isinstance(user, dict)
        ]
        return RemoteIssue(
            id=int(data["id"]),
            number=int(data["iid"]),
            title=str(data.get("title") or ""),
            body=str(data.get("description") or ""),
            state=_parse_state(data.get("state")),
            labels=labels,
            assignees=[name for name in assignees if name],
            milestone_number=int(milestone["iid"]) if milestone.get("iid") else None,
            milestone_title=milestone.get("title"),
            updated_at=data.get("updated_at"),
            url=data.get("web_url"),
        )

    @staticmethod
    def _parse_milestone(data: dict[str, Any]) -> RemoteMilestone:
        return RemoteMilestone(
            id=int(data["id"]),
            number=int(data["iid"]),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            state=_parse_state(data.get("state")),
            due_on=_parse_date(data.get("due_date")),
            updated_at=data.get("updated_at"),
            url=data.get("web_url"),
        )

    # --------------------------------------------------------------- pagination

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 1
        base_params: dict[str, Any] = {"per_page": 100, **(params or {})}
        while True:
            response = self.client.request("GET", path, params={**base_params, "page": page})
            raise_for_status(response, context=f"GET {path}")
            data = response.json()
            if not isinstance(data, list):
                raise ProviderError(f"GET {path}: expected a JSON array from the GitLab API")
            results.extend(item for item in data if isinstance(item, dict))
            next_page = response.headers.get("x-next-page", "").strip()
            if not next_page:
                break
            page = int(next_page)
        return results
