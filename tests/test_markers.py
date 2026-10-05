"""Tests for identity markers."""

from __future__ import annotations

import pytest

from repo_planner.markers import ensure_marker, find_markers, has_marker, marker


def test_marker_format() -> None:
    assert marker("issue", "i1") == "<!-- repo-planner:issue:i1 -->"
    assert marker("milestone", "m-2") == "<!-- repo-planner:milestone:m-2 -->"


def test_marker_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        marker("pull-request", "x")


def test_has_marker_and_find_markers() -> None:
    text = "Hello\n\n<!-- repo-planner:issue:i1 -->\n<!-- repo-planner:milestone:m1 -->\n"
    assert has_marker(text, "issue", "i1")
    assert not has_marker(text, "issue", "i2")
    assert find_markers(text) == [("issue", "i1"), ("milestone", "m1")]
    assert find_markers(None) == []
    assert find_markers("no markers here") == []


def test_ensure_marker_appends_once() -> None:
    once = ensure_marker("Body text\n", "issue", "i1")
    assert once.endswith("<!-- repo-planner:issue:i1 -->\n")
    assert "Body text" in once
    twice = ensure_marker(once, "issue", "i1")
    assert once == twice


def test_ensure_marker_with_empty_text() -> None:
    assert ensure_marker("", "milestone", "m1") == "<!-- repo-planner:milestone:m1 -->\n"


def test_ensure_marker_keeps_blank_line_separation() -> None:
    result = ensure_marker("# Title\n\nParagraph", "issue", "i1")
    assert result == "# Title\n\nParagraph\n\n<!-- repo-planner:issue:i1 -->\n"
