"""Tests for state persistence and atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from repo_planner.errors import StateError
from repo_planner.files import atomic_write_json, atomic_write_text
from repo_planner.models import ProjectState
from repo_planner.state import load_state, record_resource, save_state, state_scope_warning
from support import FIXED_NOW, make_issue


def test_load_missing_state_yields_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "configs" / "state.json")
    assert state.milestones == {}
    assert state.issues == {}


def test_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = ProjectState(provider="github", repository="owner/repo")
    assert record_resource(state, "issue", "i1", make_issue(4)) is True
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.issues["i1"].number == 4
    assert loaded.issues["i1"].updated_at == FIXED_NOW.astimezone(FIXED_NOW.tzinfo)
    assert json.loads(path.read_text(encoding="utf-8"))["issues"]["i1"]["number"] == 4


def test_record_resource_reports_changes() -> None:
    state = ProjectState()
    issue = make_issue(4)
    assert record_resource(state, "issue", "i1", issue) is True
    assert record_resource(state, "issue", "i1", issue) is False
    assert record_resource(state, "issue", "i1", make_issue(5)) is True


def test_invalid_json_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)


def test_invalid_shape_raises_state_error(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"issues": {"i1": {"id": "nope"}}}), encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)


def test_atomic_write_creates_file_without_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data.json"
    atomic_write_json(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    leftovers = [item for item in path.parent.iterdir() if item.suffix == ".tmp"]
    assert leftovers == []


def test_atomic_write_failure_preserves_previous_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.json"
    atomic_write_text(path, "original")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(path, "replacement")

    assert path.read_text(encoding="utf-8") == "original"
    leftovers = [item for item in tmp_path.iterdir() if item.suffix == ".tmp"]
    assert leftovers == []


def test_state_scope_warning() -> None:
    state = ProjectState(provider="github", repository="owner/repo")
    assert state_scope_warning(state, provider="github", repository="owner/repo") is None
    assert state_scope_warning(state, provider="gitlab", repository="group/project") is not None
    empty = ProjectState()
    assert state_scope_warning(empty, provider="github", repository="owner/repo") is None
