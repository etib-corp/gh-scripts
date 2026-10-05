"""Loading, saving and updating the local state file (``configs/state.json``)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .diff import normalize_timestamp
from .errors import StateError
from .files import atomic_write_json
from .models import ProjectState, RemoteIssue, RemoteMilestone, ResourceState


def _format_validation_error(exc: ValidationError) -> str:
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)


def load_state(path: Path) -> ProjectState:
    """Load the state file; a missing file yields an empty state."""

    path = Path(path)
    if not path.exists():
        return ProjectState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StateError(f"{path}: cannot read state file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"{path}: invalid JSON in state file: {exc}") from exc
    try:
        return ProjectState.model_validate(raw)
    except ValidationError as exc:
        raise StateError(f"{path}: invalid state file:\n{_format_validation_error(exc)}") from exc


def save_state(path: Path, state: ProjectState) -> None:
    """Atomically persist the state file."""

    atomic_write_json(path, state.model_dump(mode="json"))


def record_resource(
    state: ProjectState,
    kind: str,
    key: str,
    remote: RemoteIssue | RemoteMilestone,
) -> bool:
    """Record a resolved remote resource in the state. Returns True on change."""

    entry = ResourceState(
        id=remote.id,
        number=remote.number,
        updated_at=normalize_timestamp(remote.updated_at),
        url=remote.url,
    )
    bucket = state.bucket(kind)
    if bucket.get(key) == entry:
        return False
    bucket[key] = entry
    return True


def state_scope_warning(state: ProjectState, *, provider: str, repository: str) -> str | None:
    """Warn when the state file was recorded for another provider/repository."""

    if not state.repository:
        return None
    if state.repository != repository or (state.provider and state.provider != provider):
        recorded = f"{state.provider or '?'}:{state.repository}"
        return (
            f"state file scope ({recorded}) differs from the configured repository "
            f"({provider}:{repository}); resolve remote IDs may be stale"
        )
    return None
