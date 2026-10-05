"""Smoke tests for the CLI surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_planner import cli
from repo_planner.state import load_state
from support import FakeProvider, make_milestone, make_project


def run(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    provider: FakeProvider | None = None,
) -> int:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    if provider is not None:
        monkeypatch.setattr(cli, "create_provider", lambda config, *, token, **kwargs: provider)
    return cli.main(argv)


def test_init_scaffolds_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    code = run(
        monkeypatch,
        ["init", "--root", str(tmp_path), "--provider", "github", "--repository", "owner/repo"],
    )
    assert code == 0
    assert (tmp_path / "configs" / "repo-planner.json").is_file()
    assert (tmp_path / "configs" / "manifest.json").is_file()
    assert (tmp_path / "configs" / "state.json").is_file()
    assert "created" in capsys.readouterr().out


def test_init_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    code = run(
        monkeypatch,
        ["init", "--root", str(tmp_path), "--repository", "owner/repo", "--dry-run"],
    )
    assert code == 0
    assert not (tmp_path / "configs").exists()
    assert "dry run" in capsys.readouterr().out


def test_init_requires_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    code = run(monkeypatch, ["init", "--root", str(tmp_path)])
    assert code == 1
    assert "--repository" in capsys.readouterr().err


def test_validate_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    make_project(tmp_path)
    code = run(monkeypatch, ["validate", "--root", str(tmp_path)])
    assert code == 0
    assert "manifest ok" in capsys.readouterr().out


def test_validate_reports_missing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    (tmp_path / "docs" / "i2.md").unlink()
    code = run(monkeypatch, ["validate", "--root", str(tmp_path)])
    assert code == 1
    assert "does not exist" in capsys.readouterr().err


def test_validate_reports_invalid_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    bad = {
        "issues": [{"key": "i1", "title": "I", "body_file": "docs/i1.md", "depends_on": ["ghost"]}]
    }
    make_project(tmp_path, manifest=bad)
    code = run(monkeypatch, ["validate", "--root", str(tmp_path)])
    assert code == 1
    assert "unknown dependency" in capsys.readouterr().err


def test_plan_with_fake_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    make_project(tmp_path)
    provider = FakeProvider(labels=["bug"])
    code = run(monkeypatch, ["plan", "--root", str(tmp_path)], provider=provider)
    out = capsys.readouterr().out
    assert code == 0
    assert "plan: 3 to create" in out
    assert "create milestone m1" in out


def test_plan_fails_on_missing_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    provider = FakeProvider(labels=[])
    code = run(monkeypatch, ["plan", "--root", str(tmp_path)], provider=provider)
    assert code == 1
    assert "missing labels" in capsys.readouterr().err


def test_publish_defaults_to_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    provider = FakeProvider(labels=["bug"])
    code = run(monkeypatch, ["publish", "all", "--root", str(tmp_path)], provider=provider)
    out = capsys.readouterr().out
    assert code == 0
    assert "dry run" in out
    assert provider.mutations == []


def test_publish_apply_with_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    make_project(tmp_path)
    provider = FakeProvider(labels=["bug"])
    code = run(
        monkeypatch,
        ["publish", "all", "--apply", "--yes", "--root", str(tmp_path)],
        provider=provider,
    )
    assert code == 0
    assert len(provider.mutations) == 3
    state = load_state(tmp_path / "configs" / "state.json")
    assert set(state.issues) == {"i1", "i2"}
    assert set(state.milestones) == {"m1"}


def test_publish_unknown_only_key_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    code = run(
        monkeypatch,
        ["publish", "all", "--only", "nope", "--root", str(tmp_path)],
        provider=FakeProvider(),
    )
    assert code == 1
    assert "unknown key" in capsys.readouterr().err


def test_sync_milestones_writes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    provider = FakeProvider(
        milestones=[
            make_milestone(2, description="managed\n\n<!-- repo-planner:milestone:m1 -->\n")
        ]
    )
    code = run(monkeypatch, ["sync", "milestones", "--root", str(tmp_path)], provider=provider)
    assert code == 0
    cache_file = tmp_path / "configs" / "milestones.cache.json"
    assert cache_file.is_file()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["milestones"][0]["number"] == 2
    assert data["managed"] == {"m1": 2}


def test_dry_run_and_apply_are_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    make_project(tmp_path)
    code = run(
        monkeypatch,
        ["publish", "all", "--dry-run", "--apply", "--root", str(tmp_path)],
        provider=FakeProvider(),
    )
    assert code == 1
    assert "mutually exclusive" in capsys.readouterr().err
