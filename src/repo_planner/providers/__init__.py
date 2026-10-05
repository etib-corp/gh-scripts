"""Provider adapters for GitHub and GitLab."""

from __future__ import annotations

from typing import Any

from ..config import ProjectConfig
from .base import Provider
from .github import GitHubProvider
from .gitlab import GitLabProvider

__all__ = ["GitHubProvider", "GitLabProvider", "Provider", "create_provider"]


def create_provider(config: ProjectConfig, *, token: str, **kwargs: Any) -> Provider:
    """Instantiate the provider adapter matching the configuration."""

    if config.provider == "github":
        return GitHubProvider(config.repository, token, api_url=config.api_base_url, **kwargs)
    if config.provider == "gitlab":
        return GitLabProvider(config.repository, token, api_url=config.api_base_url, **kwargs)
    raise ValueError(f"unsupported provider: {config.provider!r}")
