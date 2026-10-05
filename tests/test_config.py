"""Tests for project configuration loading and git remote detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_planner.config import (
    ProjectConfig,
    detect_git_origin,
    load_project,
    load_token,
    scaffold_project,
)
from repo_planner.errors import ConfigurationError
from support import make_project


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_project(tmp_path)
    assert "repo-planner init" in str(excinfo.value)


def test_github_repository_shape_rejected(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "repo-planner.json").write_text(
        '{"provider": "github", "repository": "a/b/c"}', encoding="utf-8"
    )
    with pytest.raises(ConfigurationError):
        load_project(tmp_path)


def test_gitlab_nested_group_repository_allowed() -> None:
    config = ProjectConfig.model_validate({"provider": "gitlab", "repository": "group/sub/repo"})
    assert config.repository == "group/sub/repo"


def test_token_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ProjectConfig(repository="owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    assert load_token(config) == "secret"
    monkeypatch.delenv("GITHUB_TOKEN")
    with pytest.raises(ConfigurationError) as excinfo:
        load_token(config)
    assert "GITHUB_TOKEN" in str(excinfo.value)


def test_custom_token_env() -> None:
    config = ProjectConfig(repository="owner/repo", token_env="MY_TOKEN")
    assert config.token_env_name == "MY_TOKEN"


def test_detect_git_origin_github_ssh(tmp_path: Path) -> None:
    write_git_config(tmp_path, "git@github.com:owner/repo.git")
    assert detect_git_origin(tmp_path) == ("github", "owner/repo", "github.com")


def test_detect_git_origin_gitlab_https_nested(tmp_path: Path) -> None:
    write_git_config(tmp_path, "https://gitlab.com/group/sub/repo.git")
    assert detect_git_origin(tmp_path) == ("gitlab", "group/sub/repo", "gitlab.com")


def test_detect_git_origin_unknown_host(tmp_path: Path) -> None:
    write_git_config(tmp_path, "https://git.example.com/team/repo.git")
    provider, repository, host = detect_git_origin(tmp_path)  # type: ignore[misc]
    assert provider is None
    assert repository == "team/repo"
    assert host == "git.example.com"


def test_detect_git_origin_without_git_dir(tmp_path: Path) -> None:
    assert detect_git_origin(tmp_path) is None


def test_scaffold_project_creates_and_preserves(tmp_path: Path) -> None:
    results = scaffold_project(tmp_path, provider="github", repository="owner/repo")
    statuses = {path.name: status for path, status in results}
    assert statuses == {
        "repo-planner.json": "created",
        "manifest.json": "created",
        "state.json": "created",
    }
    assert (tmp_path / "configs" / "manifest.json").is_file()

    # Second run without force keeps the existing files.
    results = scaffold_project(tmp_path, provider="github", repository="owner/repo")
    assert {status for _, status in results} == {"kept"}

    # Dry run reports without writing.
    results = scaffold_project(
        tmp_path, provider="gitlab", repository="group/repo", force=True, dry_run=True
    )
    assert {status for _, status in results} == {"would overwrite"}


def test_scaffold_validates_repository(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        scaffold_project(tmp_path, provider="github", repository="too/many/parts")


def test_make_project_fixture_roundtrip(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert project.config.repository == "owner/repo"
    assert project.manifest_path.name == "manifest.json"
    assert project.state_path.name == "state.json"


def write_git_config(root: Path, url: str) -> None:
    git_dir = root / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text(
        '[remote "origin"]\n\turl = ' + url + "\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )
