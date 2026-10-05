"""Tests for applying plans (the publish path)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from repo_planner.config import Project
from repo_planner.errors import ManifestValidationError, RemoteConflictError
from repo_planner.manifest import load_manifest
from repo_planner.models import ProjectState, ResourceState
from repo_planner.reconciler import Reconciler
from repo_planner.state import load_state
from repo_planner.templates import TemplateRenderer
from support import FIXED_NOW, FakeProvider, make_issue, make_milestone


def make_reconciler(
    project: Project,
    provider: FakeProvider,
    state: ProjectState | None = None,
) -> Reconciler:
    manifest = load_manifest(project.manifest_path, root=project.root)
    return Reconciler(
        root=project.root,
        config=project.config,
        manifest=manifest,
        state=state if state is not None else ProjectState(),
        provider=provider,
        renderer=TemplateRenderer(),
        state_path=project.state_path,
    )


def test_execute_creates_resources_in_order(project: Project, fake_provider: FakeProvider) -> None:
    reconciler = make_reconciler(project, fake_provider)
    summary = reconciler.execute(reconciler.plan())
    assert summary.created_milestones == 1
    assert summary.created_issues == 2
    assert fake_provider.mutations == [
        "create milestone #1",
        "create issue #1",
        "create issue #2",
    ]
    state = load_state(project.state_path)
    assert set(state.milestones) == {"m1"}
    assert set(state.issues) == {"i1", "i2"}
    assert state.issues["i1"].updated_at is not None


def test_dependency_links_rendered_after_dependency_created(
    project: Project, fake_provider: FakeProvider
) -> None:
    reconciler = make_reconciler(project, fake_provider)
    reconciler.execute(reconciler.plan())
    second = fake_provider.issue_by_number(2)
    assert second.title == "Second issue"
    assert "https://fake/issues/1" in second.body
    assert "<!-- repo-planner:issue:i2 -->" in second.body


def test_second_run_is_a_no_op(project: Project, fake_provider: FakeProvider) -> None:
    reconciler = make_reconciler(project, fake_provider)
    reconciler.execute(reconciler.plan())
    mutations_before = len(fake_provider.mutations)

    state = load_state(project.state_path)
    reconciler2 = make_reconciler(project, fake_provider, state=state)
    plan = reconciler2.plan()
    assert all(resource.action == "skip" for resource in plan.resources)
    summary = reconciler2.execute(plan)
    assert summary.skipped == 3
    assert len(fake_provider.mutations) == mutations_before


def test_missing_labels_block_execute(project: Project) -> None:
    provider = FakeProvider(labels=[])
    reconciler = make_reconciler(project, provider)
    plan = reconciler.plan()
    with pytest.raises(ManifestValidationError) as excinfo:
        reconciler.execute(plan)
    assert "missing labels" in str(excinfo.value)
    assert provider.mutations == []


def test_create_missing_labels_flag(project: Project) -> None:
    provider = FakeProvider(labels=[])
    reconciler = make_reconciler(project, provider)
    plan = reconciler.plan()
    summary = reconciler.execute(plan, create_missing_labels=True)
    assert provider.created_labels == ["bug"]
    assert summary.created_labels == ["bug"]
    assert provider.mutations[0] == "create label 'bug'"


def test_conflict_blocks_until_forced(project: Project) -> None:
    changed_at = FIXED_NOW + timedelta(hours=1)
    remote = make_issue(1, title="Old title", body="old", updated_at=changed_at)
    provider = FakeProvider(labels=["bug"], issues=[remote])
    state = ProjectState()
    state.issues["i1"] = ResourceState(id=remote.id, number=1, updated_at=FIXED_NOW)
    reconciler = make_reconciler(project, provider, state=state)
    plan = reconciler.plan()

    with pytest.raises(RemoteConflictError):
        reconciler.execute(plan)
    assert provider.mutations == []

    summary = reconciler.execute(plan, force_update=True)
    assert any("forced over remote conflict" in message for message in summary.messages)
    assert "update issue #1" in provider.mutations


def test_skipped_resources_persist_resolution(project: Project) -> None:
    body = "Body for First issue.\n\n<!-- repo-planner:issue:i1 -->\n"
    remote = make_issue(
        1,
        title="First issue",
        body=body,
        labels=["bug"],
        milestone_number=4,
        milestone_title="Milestone One",
    )
    provider = FakeProvider(
        labels=["bug"],
        milestones=[make_milestone(4, title="Milestone One")],
        issues=[remote],
    )
    reconciler = make_reconciler(project, provider)
    plan = reconciler.plan(only={"i1"})
    assert [resource.action for resource in plan.issues] == ["skip"]
    reconciler.execute(plan)
    state = load_state(project.state_path)
    assert state.issues["i1"].number == 1
