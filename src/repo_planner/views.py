"""Template context views for manifests, milestones, issues and repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import IssueSpec, Manifest, MilestoneSpec, RemoteIssue, RemoteMilestone

if TYPE_CHECKING:
    from .config import ProjectConfig


def web_url(config: ProjectConfig) -> str:
    """Best-effort web URL of the configured repository."""

    api_url = config.api_url
    if api_url:
        base = api_url
        for suffix in ("/api/v4", "/api/v3"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return f"{base.rstrip('/')}/{config.repository}"
    if config.provider == "gitlab":
        return f"https://gitlab.com/{config.repository}"
    return f"https://github.com/{config.repository}"


def repository_dict(config: ProjectConfig) -> dict[str, Any]:
    """Template context for the ``repository`` variable."""

    parts = config.repository.split("/")
    return {
        "provider": config.provider,
        "full_name": config.repository,
        "namespace": "/".join(parts[:-1]),
        "name": parts[-1],
        "url": web_url(config),
    }


def milestone_ref_dict(
    spec: MilestoneSpec,
    remote: RemoteMilestone | None = None,
) -> dict[str, Any]:
    """Template context for a milestone (desired state plus resolved remote info)."""

    return {
        "key": spec.key,
        "title": spec.title,
        "state": spec.state,
        "due_on": spec.due_on.isoformat() if spec.due_on else None,
        "metadata": dict(spec.metadata),
        "number": remote.number if remote else None,
        "url": remote.url if remote else None,
        "id": remote.id if remote else None,
    }


def issue_ref_dict(spec: IssueSpec, remote: RemoteIssue | None = None) -> dict[str, Any]:
    """Template context for an issue (desired state plus resolved remote info)."""

    return {
        "key": spec.key,
        "title": spec.title,
        "state": spec.state,
        "labels": list(spec.labels),
        "assignees": list(spec.assignees),
        "depends_on": list(spec.depends_on),
        "milestone": spec.milestone,
        "metadata": dict(spec.metadata),
        "number": remote.number if remote else None,
        "url": remote.url if remote else None,
        "id": remote.id if remote else None,
    }


def static_context(
    config: ProjectConfig,
    manifest: Manifest,
    *,
    milestone: MilestoneSpec | None = None,
    issue: IssueSpec | None = None,
) -> dict[str, Any]:
    """Offline template context: manifest data with no remote information."""

    issues = {spec.key: issue_ref_dict(spec) for spec in manifest.issues}
    milestone_view = milestone_ref_dict(milestone) if milestone is not None else None
    issue_view = issue_ref_dict(issue) if issue is not None else None
    if issue is not None and issue.milestone:
        referenced = manifest.milestone_by_key(issue.milestone)
        if referenced is not None:
            milestone_view = milestone_ref_dict(referenced)
    metadata: dict[str, Any] = {}
    if issue is not None:
        metadata = dict(issue.metadata)
    elif milestone is not None:
        metadata = dict(milestone.metadata)
    return {
        "repository": repository_dict(config),
        "milestone": milestone_view,
        "issue": issue_view,
        "issues": issues,
        "metadata": metadata,
    }
