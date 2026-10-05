"""Tests for the plan phase of the reconciliation engine."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from repo_planner.config import Project
from repo_planner.errors import AmbiguousResourceError
from repo_planner.manifest import load_manifest
from repo_planner.models import ProjectState, ResourceState
from repo_planner.reconciler import Reconciler
from repo_planner.templates import TemplateRenderer
from support import FIXED_NOW, FakeProvider, make_issue, make_milestone, make_project


def make_reconciler(
    project: Project,
    provider: FakeProvider,
    state: ProjectState | None = None,
    state_path: Path | None = None,
) -> Reconciler:
    manifest = load_manifest(project.manifest_path, root=project.root)
    return Reconciler(
        root=project.root,
        config=project.config,
        manifest=manifest,
        state=state if state is not None else ProjectState(),
        provider=provider,
        renderer=TemplateRenderer(),
        state_path=state_path,
    )


def test_plan_creates_everything(project: Project, fake_provider: FakeProvider) -> None:
    reconciler = make_reconciler(project, fake_provider)
    plan = reconciler.plan()
    assert [resource.key for resource in plan.milestones] == ["m1"]
    assert [resource.key for resource in plan.issues] == ["i1", "i2"]
    assert all(resource.action == "create" for resource in plan.resources)
    assert plan.missing_labels == []
    assert plan.missing_assignees == []


def test_dependency_order_independent_of_manifest_order(tmp_path: Path) -> None:
    manifest = {
        "milestones": [],
        "issues": [
            {"key": "i2", "title": "Second", "body_file": "docs/i2.md", "depends_on": ["i1"]},
            {"key": "i1", "title": "First", "body_file": "docs/i1.md"},
        ],
    }
    project = make_project(tmp_path, manifest=manifest)
    plan = make_reconciler(project, FakeProvider()).plan()
    assert [resource.key for resource in plan.issues] == ["i1", "i2"]


def test_resolution_prefers_state_id(project: Project) -> None:
    remote = make_issue(7, title="Old title", body="old", updated_at=FIXED_NOW)
    provider = FakeProvider(labels=["bug"], issues=[remote])
    state = ProjectState()
    state.issues["i1"] = ResourceState(id=remote.id, number=7, updated_at=FIXED_NOW)
    plan = make_reconciler(project, provider, state=state).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.matched_by == "state"
    assert i1.action == "update"
    assert i1.conflict is False


def test_resolution_falls_back_to_marker(project: Project) -> None:
    body = "old body\n\n<!-- repo-planner:issue:i1 -->\n"
    provider = FakeProvider(
        labels=["bug"], issues=[make_issue(3, title="Totally different", body=body)]
    )
    plan = make_reconciler(project, provider).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.matched_by == "marker"
    assert i1.action == "update"


def test_resolution_falls_back_to_exact_title(project: Project) -> None:
    provider = FakeProvider(labels=["bug"], issues=[make_issue(4, title="First issue", body="old")])
    plan = make_reconciler(project, provider).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.matched_by == "title"


def test_ambiguous_marker_fails(project: Project) -> None:
    marker = "<!-- repo-planner:issue:i1 -->"
    provider = FakeProvider(
        labels=["bug"],
        issues=[
            make_issue(1, title="A", body=marker),
            make_issue(2, title="B", body=marker),
        ],
    )
    with pytest.raises(AmbiguousResourceError) as excinfo:
        make_reconciler(project, provider).plan()
    assert "#1" in str(excinfo.value) and "#2" in str(excinfo.value)


def test_missing_labels_and_assignees_are_reported(tmp_path: Path) -> None:
    manifest = {
        "milestones": [{"key": "m1", "title": "Milestone One"}],
        "issues": [
            {
                "key": "i1",
                "title": "First issue",
                "body_file": "docs/i1.md",
                "labels": ["bug", "docs"],
                "assignees": ["zoe"],
            }
        ],
    }
    project = make_project(tmp_path, manifest=manifest)
    provider = FakeProvider(labels=["bug"], assignees=["alice"])
    reconciler = make_reconciler(project, provider)
    plan = reconciler.plan()
    assert plan.missing_labels == ["docs"]
    assert plan.missing_assignees == ["zoe"]
    problems = reconciler.preflight_problems(plan)
    assert {problem.kind for problem in problems} == {"labels", "assignees"}
    assert reconciler.preflight_problems(plan, create_missing_labels=True)[0].kind == "assignees"
    assert provider.mutations == []


def test_only_filters_resources(project: Project, fake_provider: FakeProvider) -> None:
    plan = make_reconciler(project, fake_provider).plan(only={"i1"})
    assert plan.milestones == []
    assert [resource.key for resource in plan.issues] == ["i1"]


def test_kinds_filter_resources(project: Project, fake_provider: FakeProvider) -> None:
    plan = make_reconciler(project, fake_provider).plan(kinds={"issue"})
    assert plan.milestones == []
    assert len(plan.issues) == 2


def test_issue_payload_links_resolved_milestone(project: Project) -> None:
    provider = FakeProvider(labels=["bug"], milestones=[make_milestone(4, title="Milestone One")])
    plan = make_reconciler(project, provider).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.payload is not None
    assert i1.payload.milestone_number == 4


def test_issue_payload_milestone_none_when_unresolved(project: Project) -> None:
    plan = make_reconciler(project, FakeProvider(labels=["bug"])).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.payload is not None
    assert i1.payload.milestone_number is None


def test_conflict_detected_when_remote_changed(project: Project) -> None:
    changed_at = FIXED_NOW + timedelta(hours=1)
    remote = make_issue(1, title="First issue", body="old", updated_at=changed_at)
    provider = FakeProvider(labels=["bug"], issues=[remote])
    state = ProjectState()
    state.issues["i1"] = ResourceState(id=remote.id, number=1, updated_at=FIXED_NOW)
    reconciler = make_reconciler(project, provider, state=state)
    plan = reconciler.plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.conflict is True
    assert i1.conflict_detail is not None
    assert "remote changed" in i1.conflict_detail
    problems = reconciler.preflight_problems(plan)
    conflict_problems = [problem for problem in problems if problem.kind == "conflict"]
    assert len(conflict_problems) == 1
    assert reconciler.preflight_problems(plan, force_update=True) == []


def test_no_conflict_when_timestamp_matches(project: Project) -> None:
    remote = make_issue(1, title="Old", body="old", updated_at=FIXED_NOW)
    provider = FakeProvider(labels=["bug"], issues=[remote])
    state = ProjectState()
    state.issues["i1"] = ResourceState(id=remote.id, number=1, updated_at=FIXED_NOW)
    plan = make_reconciler(project, provider, state=state).plan()
    i1 = next(resource for resource in plan.issues if resource.key == "i1")
    assert i1.action == "update"
    assert i1.conflict is False
