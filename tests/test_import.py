"""Tests for ``import-existing``."""

from __future__ import annotations

from io import StringIO

import pytest

from repo_planner.config import Project
from repo_planner.console import Console
from repo_planner.errors import AmbiguousResourceError
from repo_planner.importer import import_existing
from repo_planner.manifest import load_manifest
from repo_planner.models import ProjectState, ResourceState
from support import FakeProvider, make_issue, make_milestone


def make_console() -> Console:
    return Console(stdout=StringIO(), stderr=StringIO())


def load(project: Project):
    return load_manifest(project.manifest_path, root=project.root)


def test_import_by_marker(project: Project) -> None:
    remote = make_milestone(
        2,
        title="Renamed remotely",
        description="Managed\n\n<!-- repo-planner:milestone:m1 -->\n",
    )
    provider = FakeProvider(milestones=[remote])
    state = ProjectState()
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"milestone"},
        dry_run=False,
        prompt=None,
        console=make_console(),
    )
    assert outcome.changed is True
    assert state.milestones["m1"].number == 2
    assert outcome.attached and "identity marker" in outcome.attached[0]


def test_import_skips_already_mapped(project: Project) -> None:
    provider = FakeProvider()
    state = ProjectState()
    state.issues["i1"] = ResourceState(id=1, number=1)
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"issue"},
        dry_run=False,
        prompt=None,
        console=make_console(),
    )
    assert any("already mapped" in line for line in outcome.skipped)


def test_import_without_prompt_skips_title_matches(project: Project) -> None:
    provider = FakeProvider(issues=[make_issue(5, title="First issue")])
    state = ProjectState()
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"issue"},
        dry_run=False,
        prompt=None,
        console=make_console(),
    )
    assert state.issues == {}
    assert outcome.attached == []
    assert any("explicit selection" in line for line in outcome.skipped)


def test_import_interactive_selection(project: Project) -> None:
    provider = FakeProvider(issues=[make_issue(9, title="First issue")])
    state = ProjectState()
    answers = iter(["1", "s"])
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"issue"},
        dry_run=False,
        prompt=lambda _message: next(answers),
        console=make_console(),
    )
    assert state.issues["i1"].number == 9
    assert "explicit selection" in outcome.attached[0]


def test_import_manual_remote_number(project: Project) -> None:
    provider = FakeProvider(issues=[make_issue(9, title="Unrelated title")])
    state = ProjectState()
    answers = iter(["9", "s"])
    import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"issue"},
        dry_run=False,
        prompt=lambda _message: next(answers),
        console=make_console(),
    )
    assert state.issues["i1"].number == 9


def test_import_skip_answer(project: Project) -> None:
    provider = FakeProvider(issues=[make_issue(9, title="First issue")])
    state = ProjectState()
    answers = iter(["s", "s"])
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"issue"},
        dry_run=False,
        prompt=lambda _message: next(answers),
        console=make_console(),
    )
    assert state.issues == {}
    assert outcome.attached == []


def test_import_ambiguous_marker_raises(project: Project) -> None:
    marker = "<!-- repo-planner:milestone:m1 -->"
    provider = FakeProvider(
        milestones=[
            make_milestone(1, title="A", description=marker),
            make_milestone(2, title="B", description=marker),
        ]
    )
    with pytest.raises(AmbiguousResourceError):
        import_existing(
            manifest=load(project),
            state=ProjectState(),
            provider=provider,
            kinds={"milestone"},
            dry_run=False,
            prompt=None,
            console=make_console(),
        )


def test_import_dry_run_does_not_change_state(project: Project) -> None:
    remote = make_milestone(2, title="Renamed", description="<!-- repo-planner:milestone:m1 -->")
    provider = FakeProvider(milestones=[remote])
    state = ProjectState()
    outcome = import_existing(
        manifest=load(project),
        state=state,
        provider=provider,
        kinds={"milestone"},
        dry_run=True,
        prompt=None,
        console=make_console(),
    )
    assert state.milestones == {}
    assert outcome.attached
