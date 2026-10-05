"""Project configuration: loading, validation and scaffolding."""

from __future__ import annotations

import configparser
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .errors import ConfigurationError, ManifestValidationError
from .files import atomic_write_json
from .paths import resolve_document_path

DEFAULT_CONFIG_PATH = "configs/repo-planner.json"
DEFAULT_MANIFEST_PATH = "configs/manifest.json"
DEFAULT_STATE_PATH = "configs/state.json"
DEFAULT_MILESTONE_CACHE_PATH = "configs/milestones.cache.json"

DEFAULT_TOKEN_ENV = {"github": "GITHUB_TOKEN", "gitlab": "GITLAB_TOKEN"}
DEFAULT_API_URL = {"github": "https://api.github.com", "gitlab": "https://gitlab.com/api/v4"}

_GIT_URL_RE = re.compile(
    r"^(?:(?P<scheme>[a-z+][a-z0-9+.-]*)://)?"
    r"(?:(?P<user>[^@/]+)@)?"
    r"(?P<host>[^:/]+)"
    r"(?::(?P<port>\d+))?"
    r"[:/](?P<path>.+)$"
)


class ProjectConfig(BaseModel):
    """Contents of ``configs/repo-planner.json``."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["github", "gitlab"] = "github"
    repository: str
    manifest: str = DEFAULT_MANIFEST_PATH
    state: str = DEFAULT_STATE_PATH
    milestone_cache: str = DEFAULT_MILESTONE_CACHE_PATH
    api_url: str | None = None
    token_env: str | None = None

    @model_validator(mode="after")
    def _validate_repository(self) -> ProjectConfig:
        repository = self.repository.strip()
        if not repository or any(char.isspace() for char in repository):
            raise ValueError("repository must look like 'owner/name'")
        parts = repository.split("/")
        if any(not part for part in parts):
            raise ValueError("repository must not contain empty path segments")
        if self.provider == "github" and len(parts) != 2:
            raise ValueError("GitHub repositories must be written as 'owner/name'")
        if self.provider == "gitlab" and len(parts) < 2:
            raise ValueError("GitLab repositories must be written as 'namespace/project'")
        object.__setattr__(self, "repository", repository)
        return self

    @property
    def token_env_name(self) -> str:
        """Environment variable that holds the API token."""

        return self.token_env or DEFAULT_TOKEN_ENV[self.provider]

    @property
    def api_base_url(self) -> str:
        """Base URL of the provider API."""

        return self.api_url or DEFAULT_API_URL[self.provider]


@dataclass(frozen=True)
class Project:
    """A loaded project: configuration plus resolved filesystem paths."""

    root: Path
    config: ProjectConfig
    config_path: Path
    manifest_path: Path
    state_path: Path
    cache_path: Path


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)


def load_project(root: Path | None = None, config_path: Path | None = None) -> Project:
    """Load and validate the project configuration."""

    base = Path(root) if root is not None else Path.cwd()
    base = base.expanduser()
    if not base.exists():
        raise ConfigurationError(f"project root does not exist: {base}")
    base = base.resolve()

    if config_path is None:
        config_file = base / DEFAULT_CONFIG_PATH
    else:
        config_file = Path(config_path).expanduser()
        if not config_file.is_absolute():
            config_file = Path.cwd() / config_file
        config_file = config_file.resolve()

    if not config_file.is_file():
        raise ConfigurationError(
            f"no configuration found at {config_file}\n"
            "run 'repo-planner init' to scaffold the project"
        )

    try:
        raw: Any = json.loads(config_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"{config_file}: cannot read configuration: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{config_file}: invalid JSON in configuration: {exc}") from exc

    try:
        config = ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(
            f"{config_file}: invalid configuration:\n{_format_validation_error(exc)}"
        ) from exc

    try:
        manifest_path = resolve_document_path(base, config.manifest, field="manifest")
        state_path = resolve_document_path(base, config.state, field="state")
        cache_path = resolve_document_path(base, config.milestone_cache, field="milestone_cache")
    except ManifestValidationError as exc:
        raise ConfigurationError(str(exc)) from exc

    return Project(
        root=base,
        config=config,
        config_path=config_file,
        manifest_path=manifest_path,
        state_path=state_path,
        cache_path=cache_path,
    )


def load_token(config: ProjectConfig) -> str:
    """Read the API token from the environment, failing with an actionable message."""

    name = config.token_env_name
    token = os.environ.get(name, "").strip()
    if not token:
        raise ConfigurationError(
            f"environment variable {name} is not set\n"
            f"create a {config.provider} API token with repository access and export it as {name}"
        )
    return token


def detect_git_origin(root: Path) -> tuple[str | None, str, str] | None:
    """Parse ``.git/config`` for the ``origin`` remote.

    Returns ``(provider, repository, host)`` where ``provider`` is ``None`` when
    the host does not look like GitHub or GitLab.
    """

    config_file = Path(root) / ".git" / "config"
    if not config_file.is_file():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(config_file.read_text(encoding="utf-8"))
    except (configparser.Error, OSError):
        return None
    if not parser.has_section('remote "origin"'):
        return None
    url = parser.get('remote "origin"', "url", fallback="").strip()
    if not url:
        return None
    match = _GIT_URL_RE.match(url)
    if not match:
        return None
    host = match.group("host").lower()
    path = match.group("path").strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.strip("/")
    if not path:
        return None
    if "gitlab" in host:
        provider = "gitlab"
    elif "github" in host:
        provider = "github"
    else:
        provider = None
    return provider, path, host


def scaffold_project(
    root: Path,
    *,
    provider: str,
    repository: str,
    force: bool = False,
    dry_run: bool = False,
) -> list[tuple[Path, str]]:
    """Create the configuration skeleton. Returns ``(path, status)`` pairs."""

    root = Path(root).resolve()
    try:
        config = ProjectConfig.model_validate({"provider": provider, "repository": repository})
    except ValidationError as exc:
        raise ConfigurationError(
            f"invalid provider/repository:\n{_format_validation_error(exc)}"
        ) from exc

    files: list[tuple[Path, dict[str, Any]]] = [
        (
            root / DEFAULT_CONFIG_PATH,
            {"provider": config.provider, "repository": config.repository},
        ),
        (
            root / DEFAULT_MANIFEST_PATH,
            {"version": 1, "milestones": [], "issues": []},
        ),
        (
            root / DEFAULT_STATE_PATH,
            {
                "version": 1,
                "provider": config.provider,
                "repository": config.repository,
                "milestones": {},
                "issues": {},
            },
        ),
    ]

    results: list[tuple[Path, str]] = []
    for path, payload in files:
        exists = path.exists()
        if exists and not force:
            results.append((path, "kept"))
            continue
        if dry_run:
            results.append((path, "would overwrite" if exists else "would create"))
            continue
        atomic_write_json(path, payload)
        results.append((path, "overwritten" if exists else "created"))
    return results


def configured_env_vars(config: ProjectConfig) -> list[str]:
    """Return the environment variable names that are meaningful for this config."""

    return [config.token_env_name]
