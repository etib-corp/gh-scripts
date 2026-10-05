"""Tests for the GitHub adapter: parsing, payload conversion, pagination."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest

from repo_planner.errors import (
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ResourceNotFoundError,
)
from repo_planner.models import IssuePayload, MilestonePayload
from support import MockAPI, github_provider


def issue_json(number: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 1000 + number,
        "number": number,
        "title": f"Issue {number}",
        "body": "Body",
        "state": "open",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "alice"}],
        "milestone": {"number": 3, "title": "M3"},
        "updated_at": "2026-01-01T12:00:00Z",
        "html_url": f"https://github.com/owner/repo/issues/{number}",
    }
    data.update(overrides)
    return data


def milestone_json(number: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 2000 + number,
        "number": number,
        "title": f"Milestone {number}",
        "description": "Description",
        "state": "open",
        "due_on": "2026-06-01T08:00:00Z",
        "updated_at": "2026-01-01T12:00:00Z",
        "html_url": f"https://github.com/owner/repo/milestone/{number}",
    }
    data.update(overrides)
    return data


def test_fetch_issues_skips_pull_requests() -> None:
    api = MockAPI()
    api.json(
        "GET",
        "/repos/owner/repo/issues",
        [issue_json(1), issue_json(2, pull_request={"url": "https://x"})],
    )
    provider = github_provider(api.transport())
    issues = provider.fetch_issues()
    assert [issue.number for issue in issues] == [1]
    issue = issues[0]
    assert issue.labels == ["bug"]
    assert issue.assignees == ["alice"]
    assert issue.milestone_number == 3
    assert issue.milestone_title == "M3"
    assert issue.url == "https://github.com/owner/repo/issues/1"


def test_pagination_follows_link_header() -> None:
    api = MockAPI()

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(
                200,
                json=[issue_json(1)],
                headers={
                    "Link": '<https://api.github.com/repos/owner/repo/issues?page=2>; rel="next"'
                },
            )
        return httpx.Response(200, json=[issue_json(2)])

    api.add("GET", "/repos/owner/repo/issues", handler)
    provider = github_provider(api.transport())
    assert [issue.number for issue in provider.fetch_issues()] == [1, 2]
    assert len(api.requests) == 2


def test_fetch_milestones_parses_due_on_and_state() -> None:
    api = MockAPI()
    api.json(
        "GET",
        "/repos/owner/repo/milestones",
        [milestone_json(3), milestone_json(4, state="closed", due_on=None)],
    )
    provider = github_provider(api.transport())
    milestones = provider.fetch_milestones()
    assert milestones[0].due_on == date(2026, 6, 1)
    assert milestones[0].state == "open"
    assert milestones[1].state == "closed"
    assert milestones[1].due_on is None


def test_create_issue_sends_payload_and_closes_state() -> None:
    api = MockAPI()
    api.json("POST", "/repos/owner/repo/issues", issue_json(1))
    api.json("PATCH", "/repos/owner/repo/issues/1", issue_json(1, state="closed"))
    provider = github_provider(api.transport())
    payload = IssuePayload(
        title="T",
        body="B",
        state="closed",
        labels=["bug"],
        assignees=["alice"],
        milestone_number=3,
    )
    issue = provider.create_issue(payload)
    assert issue.state == "closed"
    assert api.bodies("POST", "/repos/owner/repo/issues") == [
        {
            "title": "T",
            "body": "B",
            "labels": ["bug"],
            "assignees": ["alice"],
            "milestone": 3,
        }
    ]
    patch_body = api.bodies("PATCH", "/repos/owner/repo/issues/1")[0]
    assert patch_body["state"] == "closed"


def test_update_issue_clears_optional_fields() -> None:
    api = MockAPI()
    api.json("PATCH", "/repos/owner/repo/issues/5", issue_json(5))
    provider = github_provider(api.transport())
    provider.update_issue(5, IssuePayload(title="New"))
    body = api.bodies("PATCH", "/repos/owner/repo/issues/5")[0]
    assert body == {
        "title": "New",
        "body": "",
        "labels": [],
        "assignees": [],
        "state": "open",
        "milestone": None,
    }


def test_create_milestone_formats_due_on() -> None:
    api = MockAPI()
    api.json("POST", "/repos/owner/repo/milestones", milestone_json(1))
    provider = github_provider(api.transport())
    provider.create_milestone(MilestonePayload(title="M", due_on=date(2026, 6, 1)))
    body = api.bodies("POST", "/repos/owner/repo/milestones")[0]
    assert body["title"] == "M"
    assert body["due_on"] == "2026-06-01T00:00:00Z"


def test_create_milestone_sends_closed_state() -> None:
    api = MockAPI()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=milestone_json(1, state=body.get("state", "open")))

    api.add("POST", "/repos/owner/repo/milestones", handler)
    provider = github_provider(api.transport())
    milestone = provider.create_milestone(MilestonePayload(title="M", state="closed"))
    assert milestone.state == "closed"
    assert api.bodies("POST", "/repos/owner/repo/milestones")[0]["state"] == "closed"


def test_create_label_and_check_assignees() -> None:
    api = MockAPI()
    api.json("POST", "/repos/owner/repo/labels", {"name": "docs"})
    api.json("GET", "/repos/owner/repo/assignees", [{"login": "alice"}])
    provider = github_provider(api.transport())
    provider.create_label("docs")
    assert api.bodies("POST", "/repos/owner/repo/labels")[0] == {"name": "docs"}
    assert provider.check_assignees(["alice", "zoe"]) == ["zoe"]


def test_authorization_header_is_sent() -> None:
    api = MockAPI()
    api.json("GET", "/repos/owner/repo/labels", [])
    provider = github_provider(api.transport())
    assert provider.fetch_labels() == []
    assert api.requests[0].headers["authorization"] == "Bearer test-token"


def test_error_mapping() -> None:
    api = MockAPI()
    api.json("GET", "/repos/owner/repo/labels", {"message": "Bad credentials"}, status_code=401)
    with pytest.raises(ProviderAuthenticationError):
        github_provider(api.transport()).fetch_labels()

    api = MockAPI()
    api.json("GET", "/repos/owner/repo/labels", {"message": "Not Found"}, status_code=404)
    with pytest.raises(ResourceNotFoundError):
        github_provider(api.transport()).fetch_labels()

    api = MockAPI()
    api.json(
        "GET",
        "/repos/owner/repo/labels",
        {"message": "API rate limit exceeded"},
        status_code=403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"},
    )
    with pytest.raises(ProviderRateLimitError):
        github_provider(api.transport()).fetch_labels()
