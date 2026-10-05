"""``sync milestones``: cache remote milestones locally without mutating remotes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .config import Project, ProjectConfig
from .files import atomic_write_json
from .markers import find_markers
from .models import MilestoneCache, RemoteMilestone
from .providers.base import Provider


def build_cache(
    config: ProjectConfig,
    milestones: list[RemoteMilestone],
) -> MilestoneCache:
    """Build the cache payload from remote milestones."""

    managed: dict[str, int] = {}
    for milestone in milestones:
        for kind, key in find_markers(milestone.description):
            if kind == "milestone" and key not in managed:
                managed[key] = milestone.number
    return MilestoneCache(
        fetched_at=datetime.now(UTC).replace(microsecond=0),
        provider=config.provider,
        repository=config.repository,
        milestones=milestones,
        managed=managed,
    )


def filter_managed(
    milestones: list[RemoteMilestone],
    only: set[str] | None,
) -> list[RemoteMilestone]:
    """Keep only milestones managed by one of the given manifest keys."""

    if only is None:
        return milestones
    selected: list[RemoteMilestone] = []
    for milestone in milestones:
        keys = {key for kind, key in find_markers(milestone.description) if kind == "milestone"}
        if keys & only:
            selected.append(milestone)
    return selected


def write_cache(project: Project, cache: MilestoneCache) -> Path:
    """Atomically write the milestone cache file and return its path."""

    atomic_write_json(project.cache_path, cache.model_dump(mode="json"))
    return project.cache_path


def sync_milestones(
    *,
    project: Project,
    provider: Provider,
    only: set[str] | None = None,
) -> MilestoneCache:
    """Fetch remote milestones and build the cache (no remote changes)."""

    milestones = filter_managed(provider.list_milestones(), only)
    return build_cache(project.config, milestones)
