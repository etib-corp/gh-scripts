"""Minimal console abstraction for CLI output."""

from __future__ import annotations

import sys
from typing import TextIO


class Console:
    """Print user-facing output while keeping verbosity decisions in one place."""

    def __init__(
        self,
        *,
        verbose: bool = False,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.verbose = verbose
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr

    def print(self, message: str = "") -> None:
        """Print a normal message to stdout."""

        print(message, file=self._stdout)

    def detail(self, message: str) -> None:
        """Print a message only when verbose output is enabled."""

        if self.verbose:
            print(message, file=self._stdout)

    def warn(self, message: str) -> None:
        """Print a warning to stderr."""

        print(f"warning: {message}", file=self._stderr)

    def error(self, message: str) -> None:
        """Print an error to stderr."""

        print(message, file=self._stderr)
