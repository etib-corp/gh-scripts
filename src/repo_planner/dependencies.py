"""Dependency ordering for issue manifests."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence

from .errors import DependencyCycleError


def topological_order(nodes: Mapping[str, Sequence[str]]) -> list[str]:
    """Order ``nodes`` so that every dependency comes before its dependents.

    ``nodes`` maps a key to the list of keys it depends on. Keys that are not
    present in ``nodes`` are ignored. The order is stable: independent nodes
    keep their insertion order.

    Raises:
        DependencyCycleError: when the dependency graph contains a cycle. The
            error lists every key involved in (or blocked by) the cycle.
    """

    indegree: dict[str, int] = {key: 0 for key in nodes}
    dependents: dict[str, list[str]] = {key: [] for key in nodes}
    for key, dependencies in nodes.items():
        for dependency in dependencies:
            if dependency not in nodes:
                continue
            indegree[key] += 1
            dependents[dependency].append(key)

    queue: deque[str] = deque(key for key, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for dependent in dependents[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(nodes):
        involved = sorted(key for key, degree in indegree.items() if degree > 0)
        raise DependencyCycleError(
            "dependency cycle detected among issue keys: " + ", ".join(involved)
        )
    return order
