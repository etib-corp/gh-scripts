"""Loading and offline validation of manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .errors import ManifestValidationError, TemplateRenderError
from .models import Manifest
from .paths import read_document
from .templates import TemplateRenderer
from .views import static_context

if TYPE_CHECKING:
    from .config import ProjectConfig


def format_validation_error(exc: ValidationError) -> str:
    """Format Pydantic errors as actionable single lines."""

    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)


def load_manifest(path: Path, *, root: Path) -> Manifest:
    """Load and structurally validate the manifest file."""

    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestValidationError(
            f"manifest not found: {path}\nrun 'repo-planner init' to scaffold the project"
        ) from exc
    except OSError as exc:
        raise ManifestValidationError(f"{path}: cannot read manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"{path}: invalid JSON in manifest: {exc}") from exc

    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestValidationError(
            f"{path}: invalid manifest:\n{format_validation_error(exc)}"
        ) from exc


def validate_documents(
    root: Path,
    config: ProjectConfig,
    manifest: Manifest,
    renderer: TemplateRenderer,
    *,
    only: set[str] | None = None,
) -> list[str]:
    """Check referenced documents exist, stay inside the root and render.

    Returns a list of human-readable problems; an empty list means valid.
    """

    problems: list[str] = []

    for milestone in manifest.milestones:
        if only is not None and milestone.key not in only:
            continue
        if milestone.description_file:
            context = static_context(config, manifest, milestone=milestone)
            problems.extend(
                _check_document(
                    root,
                    milestone.description_file,
                    renderer,
                    context,
                    field=f"milestone {milestone.key!r} description_file",
                )
            )

    for issue in manifest.issues:
        if only is not None and issue.key not in only:
            continue
        context = static_context(config, manifest, issue=issue)
        problems.extend(
            _check_document(
                root,
                issue.body_file,
                renderer,
                context,
                field=f"issue {issue.key!r} body_file",
            )
        )

    return problems


def _check_document(
    root: Path,
    relative: str,
    renderer: TemplateRenderer,
    context: dict[str, object],
    *,
    field: str,
) -> list[str]:
    try:
        source = read_document(root, relative, field=field)
    except ManifestValidationError as exc:
        return [str(exc)]
    try:
        renderer.render_string(source, context, origin=f"{field} ({relative})")
    except TemplateRenderError as exc:
        return [str(exc)]
    return []
