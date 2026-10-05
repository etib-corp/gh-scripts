"""repo-planner: declarative management of GitHub and GitLab milestones and issues."""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("repo-planner")
except metadata.PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.1.0"

__all__ = ["__version__"]
