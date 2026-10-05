"""Tests for dependency ordering and cycle detection."""

from __future__ import annotations

import pytest

from repo_planner.dependencies import topological_order
from repo_planner.errors import DependencyCycleError


def test_dependencies_come_first() -> None:
    order = topological_order({"c": ["b"], "b": ["a"], "a": []})
    assert order == ["a", "b", "c"]


def test_stable_insertion_order_for_independent_nodes() -> None:
    order = topological_order({"z": [], "a": [], "m": []})
    assert order == ["z", "a", "m"]


def test_diamond_dependencies() -> None:
    order = topological_order(
        {"top": ["left", "right"], "left": ["base"], "right": ["base"], "base": []}
    )
    assert order.index("base") < order.index("left") < order.index("top")
    assert order.index("base") < order.index("right") < order.index("top")


def test_missing_dependencies_are_ignored() -> None:
    order = topological_order({"a": ["external"], "b": ["a"]})
    assert order == ["a", "b"]


def test_cycle_detection_lists_keys() -> None:
    with pytest.raises(DependencyCycleError) as excinfo:
        topological_order({"a": ["b"], "b": ["a"]})
    message = str(excinfo.value)
    assert "a" in message and "b" in message
    assert "cycle" in message


def test_self_cycle() -> None:
    with pytest.raises(DependencyCycleError):
        topological_order({"a": ["a"]})


def test_cycle_lists_dependents_of_cycle() -> None:
    with pytest.raises(DependencyCycleError) as excinfo:
        topological_order({"a": ["b"], "b": ["a"], "c": ["a"]})
    message = str(excinfo.value)
    assert "c" in message
