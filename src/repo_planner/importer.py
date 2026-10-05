"""``import-existing``: attach existing remote resources to manifest keys.

A resource is only ever attached after a reliable marker match or an explicit
user selection; title similarity alone is never used automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .console import Console
from .diff import normalize_timestamp
from .errors import AmbiguousResourceError
from .models import Manifest, ProjectState, RemoteIssue, RemoteMilestone, ResourceState
from .providers.base import Provider

Prompt = Callable[[str], str]

RemoteResource = RemoteIssue | RemoteMilestone


@dataclass
class ImportOutcome:
    """Summary of an import run."""

    attached: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    changed: bool = False


def import_existing(
    *,
    manifest: Manifest,
    state: ProjectState,
    provider: Provider,
    kinds: set[str],
    only: set[str] | None = None,
    dry_run: bool = False,
    prompt: Prompt | None = None,
    console: Console,
) -> ImportOutcome:
    """Attach remote resources to manifest keys.

    ``kinds`` contains ``"milestone"`` and/or ``"issue"``. When ``prompt`` is
    ``None`` the import is non-interactive: only marker matches are attached
    and anything else is reported as skipped.
    """

    outcome = ImportOutcome()

    if "milestone" in kinds:
        milestones = provider.list_milestones()
        for milestone_spec in manifest.milestones:
            if only is not None and milestone_spec.key not in only:
                continue
            _import_one(
                kind="milestone",
                key=milestone_spec.key,
                title=milestone_spec.title,
                resources=milestones,
                state=state,
                provider=provider,
                prompt=prompt,
                dry_run=dry_run,
                outcome=outcome,
                console=console,
            )

    if "issue" in kinds:
        issues = provider.list_issues()
        for issue_spec in manifest.issues:
            if only is not None and issue_spec.key not in only:
                continue
            _import_one(
                kind="issue",
                key=issue_spec.key,
                title=issue_spec.title,
                resources=issues,
                state=state,
                provider=provider,
                prompt=prompt,
                dry_run=dry_run,
                outcome=outcome,
                console=console,
            )

    return outcome


def _import_one(
    *,
    kind: str,
    key: str,
    title: str,
    resources: list[Any],
    state: ProjectState,
    provider: Provider,
    prompt: Prompt | None,
    dry_run: bool,
    outcome: ImportOutcome,
    console: Console,
) -> None:
    existing = state.bucket(kind).get(key)
    if existing is not None:
        outcome.skipped.append(f"{kind} {key!r}: already mapped to #{existing.number}")
        return

    marker_matches = (
        provider.find_milestones_by_marker(key)
        if kind == "milestone"
        else provider.find_issues_by_marker(key)
    )
    if len(marker_matches) > 1:
        details = ", ".join(f"#{match.number}" for match in marker_matches)
        raise AmbiguousResourceError(
            f"{kind} {key!r}: multiple remote resources carry its identity marker: {details}. "
            "Remove the duplicate marker before importing."
        )

    matched_by: str
    if len(marker_matches) == 1:
        resource: RemoteResource = marker_matches[0]
        matched_by = "identity marker"
    else:
        title_matches = [item for item in resources if item.title == title]
        if prompt is None:
            hint = ""
            if title_matches:
                hint = (
                    " (an exact title match exists but requires explicit selection; "
                    "run interactively)"
                )
            outcome.skipped.append(f"{kind} {key!r}: no identity marker found{hint}")
            return
        selected = _prompt_selection(
            kind=kind,
            key=key,
            title=title,
            candidates=title_matches,
            resources=resources,
            prompt=prompt,
            console=console,
        )
        if selected is None:
            outcome.skipped.append(f"{kind} {key!r}: selection skipped")
            return
        resource = selected
        matched_by = "explicit selection"

    if not dry_run:
        state.bucket(kind)[key] = ResourceState(
            id=resource.id,
            number=resource.number,
            updated_at=normalize_timestamp(resource.updated_at),
            url=resource.url,
        )
        outcome.changed = True
    outcome.attached.append(f"attached {kind} {key!r} -> #{resource.number} ({matched_by})")


def _prompt_selection(
    *,
    kind: str,
    key: str,
    title: str,
    candidates: list[Any],
    resources: list[Any],
    prompt: Prompt,
    console: Console,
) -> RemoteResource | None:
    console.print(f"{kind} {key!r} {title!r} has no identity marker in the remote repository.")
    for index, candidate in enumerate(candidates, start=1):
        console.print(f"  {index}) #{candidate.number} {candidate.title!r} ({candidate.state})")
    console.print("  or type the number of any remote resource, 's' to skip")

    for _ in range(3):
        answer = prompt(f"attach {kind} {key!r} to which resource? ").strip()
        if answer.casefold() in {"", "s", "skip"}:
            return None
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(candidates):
                return candidates[number - 1]
            for resource in resources:
                if resource.number == number:
                    return resource
            console.print(f"no remote {kind} with number {number}")
            continue
        console.print("enter a resource number, or 's' to skip")
    return None
