"""Tests for safe document path resolution (traversal protection)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from repo_planner.errors import ManifestValidationError
from repo_planner.paths import read_document, resolve_document_path


def test_relative_path_resolves_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "body.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    assert resolve_document_path(tmp_path, "docs/body.md") == target.resolve()


def test_absolute_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError) as excinfo:
        resolve_document_path(tmp_path, "/etc/passwd")
    assert "must be relative" in str(excinfo.value)


def test_parent_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError) as excinfo:
        resolve_document_path(tmp_path, "../outside.md")
    assert "escapes the repository root" in str(excinfo.value)


def test_nested_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError):
        resolve_document_path(tmp_path, "docs/../../outside.md")


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "docs" / "link.md"
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)
    with pytest.raises(ManifestValidationError) as excinfo:
        resolve_document_path(tmp_path, "docs/link.md")
    assert "escapes the repository root" in str(excinfo.value)


def test_empty_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError):
        resolve_document_path(tmp_path, "   ")


def test_read_document_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestValidationError) as excinfo:
        read_document(tmp_path, "docs/missing.md", field="issue 'i1' body_file")
    assert "does not exist" in str(excinfo.value)
    assert "issue 'i1' body_file" in str(excinfo.value)


def test_read_document_ok(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "body.md").write_text("content", encoding="utf-8")
    assert read_document(tmp_path, "docs/body.md") == "content"
