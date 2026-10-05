"""Safe resolution of manifest-referenced document paths.

Markdown paths declared in manifests must stay relative to the repository root
and must not escape it, e.g. through ``..`` segments or symlinks.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ManifestValidationError


def resolve_document_path(root: Path, relative: str, *, field: str = "path") -> Path:
    """Resolve ``relative`` against ``root`` and refuse anything escaping it."""

    if not relative or not relative.strip():
        raise ManifestValidationError(f"{field}: path must not be empty")
    raw = Path(relative)
    if raw.is_absolute():
        raise ManifestValidationError(
            f"{field}: {relative!r} must be relative to the repository root"
        )
    root_resolved = Path(root).resolve()
    candidate = (root_resolved / raw).resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        raise ManifestValidationError(f"{field}: {relative!r} escapes the repository root")
    return candidate


def read_document(root: Path, relative: str, *, field: str = "path") -> str:
    """Read a manifest-referenced UTF-8 document, with safe path resolution."""

    path = resolve_document_path(root, relative, field=field)
    if not path.is_file():
        raise ManifestValidationError(f"{field}: {relative!r} does not exist or is not a file")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - unusual filesystem failure
        raise ManifestValidationError(f"{field}: cannot read {relative!r}: {exc}") from exc
