"""Pytest configuration: make the test helpers importable."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from support import FakeProvider, make_project


@pytest.fixture
def project(tmp_path: Path):
    """A fully scaffolded project rooted in a temporary directory."""

    return make_project(tmp_path)


@pytest.fixture
def fake_provider() -> FakeProvider:
    """A fake provider with two labels and two assignees."""

    return FakeProvider(labels=["bug", "docs", "feature"], assignees=["alice", "bob"])
