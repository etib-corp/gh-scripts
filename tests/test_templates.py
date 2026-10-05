"""Tests for the sandboxed template renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_planner.errors import TemplateRenderError
from repo_planner.templates import TemplateRenderer


def test_render_string_with_context() -> None:
    renderer = TemplateRenderer()
    output = renderer.render_string("Hello {{ name }}!", {"name": "world"})
    assert output == "Hello world!"


def test_strict_undefined_errors() -> None:
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError) as excinfo:
        renderer.render_string("{{ missing }}", {})
    assert "missing" in str(excinfo.value)


def test_sandbox_blocks_python_internals() -> None:
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError):
        renderer.render_string("{{ ''.__class__.__mro__ }}", {})
    with pytest.raises(TemplateRenderError):
        renderer.render_string("{{ cycler.__init__.__globals__ }}", {})


def test_render_path(tmp_path: Path) -> None:
    template = tmp_path / "tmpl.md"
    template.write_text("# {{ title }}\n", encoding="utf-8")
    renderer = TemplateRenderer()
    assert renderer.render_path(template, {"title": "T"}) == "# T\n"


def test_render_missing_path(tmp_path: Path) -> None:
    renderer = TemplateRenderer()
    with pytest.raises(TemplateRenderError):
        renderer.render_path(tmp_path / "missing.md", {})
