"""Filesystem helpers used for local, atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    The content is first written to a temporary file in the same directory,
    flushed and fsynced, then moved over the destination with :func:`os.replace`.
    A failure at any point leaves the previous file untouched.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(
    path: Path,
    payload: Mapping[str, Any] | list[Any],
    *,
    indent: int = 2,
) -> None:
    """Serialize ``payload`` as UTF-8 JSON and write it atomically."""

    text = json.dumps(payload, indent=indent, ensure_ascii=False, default=str) + "\n"
    atomic_write_text(path, text)
