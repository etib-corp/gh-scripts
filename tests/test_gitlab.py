"""Tests for the GitLab adapter: parsing, payload conversion, users, encoding."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from repo_planner.models import IssuePayload, MilestonePayload
from support import MockAPI, gitlab_provider


def issue_json(iid: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 9000 + iid,
        "iid": iid,
        "title": f"Issue {iid}",
        "description": "Body",
        "state": "opened",
        "labels": ["bug"],
        "assignees": [{"username": "alice"}],
        "milestone": {"id": 300, "iid": 3, "title": "M3"},
        "updated_at": "2026-01-01T12:00:00.000Z",
        "web_url": f"https://gitlab.com/group/repo/-/issues/{iid}",
    }
    data.update(overrides)
    return data


def milestone_json(iid: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 4000 + iid,
        "iid": iid,
        "title": f"Milestone {iid}",
        "description": "Description",
        "state": "active",
        "due_date": "2026-06-01",
        "updated_at": "2026-01-01T12:00:00.000Z",
        "web_url": f"https://gitlab.com/group/repo/-/milestones/{iid}",
    }
    data.update(overrides)
    return data


def test_fetch_issues_queries_both_states_and_parses() -> None:
    api = MockAPI()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("state") == "opened":
            return httpx.Response(200, json=[issue_json(1)])
        return httpx.Response(200, json=[issue_json(2, state="closed")])

    api.add("GET", "/projects/group/repo/issues", handler)
    provider = gitlab_provider(api.transport())
    issues = provider.fetch_issues()
    assert [issue.number for issue in issues] == [1, 2]
    first = issues[0]
    assert first.labels == ["bug"]
    assert first.assignees == ["alice"]
    assert first.milestone_number == 3
    assert first.state == "open"
    assert issues[1].state == "closed"


def test_fetch_milestones_queries_both_states() -> None:
    api = MockAPI()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("state") == "active":
            return httpx.Response(200, json=[milestone_json(1)])
        return httpx.Response(200, json=[milestone_json(2, state="closed")])

    api.add("GET", "/projects/group/repo/milestones", handler)
    provider = gitlab_provider(api.transport())
    milestones = provider.fetch_milestones()
    assert [milestone.number for milestone in milestones] == [1, 2]
    assert milestones[0].due_on == date(2026, 6, 1)
    assert milestones[0].state == "open"
    assert milestones[1].state == "closed"


def test_project_path_is_url_encoded() -> None:
    api = MockAPI()
    api.json("GET", "/projects/group/sub/repo/issues", [])
    provider = gitlab_provider(api.transport(), repository="group/sub/repo")
    provider.fetch_issues()
    raw_path = api.requests[0].url.raw_path.decode()
    assert "group%2Fsub%2Frepo" in raw_path


def test_create_issue_resolves_assignee_ids() -> None:
    api = MockAPI()
    api.json("GET", "/users", [{"id": 7, "username": "alice"}])
    api.json("POST", "/projects/group/repo/issues", issue_json(1))
    provider = gitlab_provider(api.transport())
    provider.create_issue(
        IssuePayload(
            title="T",
            body="B",
            labels=["bug", "docs"],
            assignees=["alice"],
            milestone_number=3,
        )
    )
    body = api.bodies("POST", "/projects/group/repo/issues")[0]
    assert body == {
        "title": "T",
        "description": "B",
        "labels": "bug,docs",
        "assignee_ids": [7],
        "milestone_id": 3,
    }


def test_closed_issue_create_sends_state_event() -> None:
    api = MockAPI()
    api.json("POST", "/projects/group/repo/issues", issue_json(1))
    api.json("PUT", "/projects/group/repo/issues/1", issue_json(1, state="closed"))
    provider = gitlab_provider(api.transport())
    issue = provider.create_issue(IssuePayload(title="T", state="closed"))
    assert issue.state == "closed"
    assert api.bodies("PUT", "/projects/group/repo/issues/1")[0]["state_event"] == "close"


def test_update_issue_clears_optional_fields() -> None:
    api = MockAPI()
    api.json("PUT", "/projects/group/repo/issues/5", issue_json(5))
    provider = gitlab_provider(api.transport())
    provider.update_issue(5, IssuePayload(title="New"))
    body = api.bodies("PUT", "/projects/group/repo/issues/5")[0]
    assert body == {
        "title": "New",
        "description": "",
        "labels": "",
        "assignee_ids": [],
        "milestone_id": 0,
        "state_event": "reopen",
    }


def test_update_milestone_clears_due_date_and_closes() -> None:
    api = MockAPI()
    api.json("PUT", "/projects/group/repo/milestones/5", milestone_json(5))
    provider = gitlab_provider(api.transport())
    provider.update_milestone(5, MilestonePayload(title="M", state="closed"))
    body = api.bodies("PUT", "/projects/group/repo/milestones/5")[0]
    assert body["due_date"] == ""
    assert body["state_event"] == "close"


def test_check_assignees_reports_missing() -> None:
    api = MockAPI()
    api.json("GET", "/users", [])
    provider = gitlab_provider(api.transport())
    assert provider.check_assignees(["zoe"]) == ["zoe"]


def test_create_label_requires_color() -> None:
    api = MockAPI()
    api.json("POST", "/projects/group/repo/labels", {"name": "docs"})
    provider = gitlab_provider(api.transport())
    provider.create_label("docs")
    body = api.bodies("POST", "/projects/group/repo/labels")[0]
    assert body["name"] == "docs"
    assert body["color"].startswith("#")
