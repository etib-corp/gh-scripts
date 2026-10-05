"""Identity markers embedded in managed remote descriptions.

Every managed issue or milestone carries a stable HTML marker so that remote
resources can be resolved without relying on titles:

.. code-block:: html

    <!-- repo-planner:issue:<key> -->
    <!-- repo-planner:milestone:<key> -->
"""

from __future__ import annotations

import re
from typing import Final

KINDS: Final[tuple[str, ...]] = ("issue", "milestone")

_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"<!--\s*repo-planner:(issue|milestone):([A-Za-z0-9][A-Za-z0-9._-]*)\s*-->"
)


def marker(kind: str, key: str) -> str:
    """Return the identity marker for ``kind``/``key``."""

    if kind not in KINDS:
        raise ValueError(f"unknown marker kind: {kind!r} (expected 'issue' or 'milestone')")
    return f"<!-- repo-planner:{kind}:{key} -->"


def find_markers(text: str | None) -> list[tuple[str, str]]:
    """Return every ``(kind, key)`` pair found in ``text``."""

    if not text:
        return []
    return [(match.group(1), match.group(2)) for match in _MARKER_RE.finditer(text)]


def has_marker(text: str | None, kind: str, key: str) -> bool:
    """Return True when ``text`` contains the marker for ``kind``/``key``."""

    return any(
        found_kind == kind and found_key == key for found_kind, found_key in find_markers(text)
    )


def ensure_marker(text: str, kind: str, key: str) -> str:
    """Append the identity marker to ``text`` unless it is already present."""

    if has_marker(text, kind, key):
        return text
    body = text.rstrip()
    if body:
        return f"{body}\n\n{marker(kind, key)}\n"
    return f"{marker(kind, key)}\n"
