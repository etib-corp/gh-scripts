"""Restricted Jinja2 rendering for manifest-referenced Markdown templates."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .errors import TemplateRenderError


class TemplateRenderer:
    """Render Markdown templates inside a sandboxed environment.

    The environment deliberately:
    - uses ``StrictUndefined`` so typos fail loudly at plan/validate time,
    - is sandboxed so templates cannot reach arbitrary Python internals,
    - disables auto-escaping (output is Markdown, not HTML),
    - has no loader, so ``{% include %}``/``{% extends %}`` cannot read files.
    """

    def __init__(self) -> None:
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    def render_string(
        self,
        source: str,
        context: Mapping[str, Any],
        *,
        origin: str = "<string>",
    ) -> str:
        """Render template source with an explicit, restricted context."""

        try:
            template = self._env.from_string(source)
            return template.render(**context)
        except TemplateError as exc:
            raise TemplateRenderError(f"{origin}: {exc}") from exc

    def render_path(
        self,
        path: Path,
        context: Mapping[str, Any],
        *,
        origin: str | None = None,
    ) -> str:
        """Read and render a template file."""

        label = origin or str(path)
        try:
            source = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise TemplateRenderError(f"{label}: cannot read template: {exc}") from exc
        return self.render_string(source, context, origin=label)
