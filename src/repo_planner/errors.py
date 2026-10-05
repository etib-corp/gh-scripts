"""Domain-specific exception hierarchy for repo-planner."""

from __future__ import annotations


class RepoPlannerError(Exception):
    """Base class for every error raised by repo-planner.

    The CLI maps these errors to a non-zero exit code and a concise message.
    """

    exit_code = 1


class ConfigurationError(RepoPlannerError):
    """The project configuration is missing, invalid or inconsistent."""


class ManifestValidationError(RepoPlannerError):
    """A manifest (or a document it references) failed validation."""


class TemplateRenderError(RepoPlannerError):
    """A Jinja2 template could not be rendered."""


class ResourceNotFoundError(RepoPlannerError):
    """A remote resource referenced by the configuration does not exist."""


class AmbiguousResourceError(RepoPlannerError):
    """Several remote resources match the same manifest key."""


class RemoteConflictError(RepoPlannerError):
    """A remote resource changed outside repo-planner since the last run."""


class DependencyCycleError(RepoPlannerError):
    """Issue dependencies contain a cycle."""


class StateError(RepoPlannerError):
    """The local state file is missing, unreadable or corrupt."""


class ProviderError(RepoPlannerError):
    """Generic upstream provider failure (network, HTTP, protocol)."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected the credentials or the permission scope."""


class ProviderRateLimitError(ProviderError):
    """The provider rate limit was exhausted."""
