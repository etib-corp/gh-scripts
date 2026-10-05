"""Shared HTTP client with retries, exponential backoff and rate-limit handling.

No request or response headers are ever logged by this module; helpers are
provided to redact sensitive headers if callers need to debug.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ResourceNotFoundError,
)

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_SECRET_HEADERS = frozenset({"authorization", "private-token", "proxy-authorization", "cookie"})

_ERROR_MESSAGE_LIMIT = 280


class HttpClient:
    """A thin, retrying wrapper around :class:`httpx.Client`."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_backoff: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_retries = max(0, max_retries)
        self._backoff_base = backoff_base
        self._max_backoff = max_backoff
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=dict(headers or {}),
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying client."""

        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        retry_safe: bool = False,
    ) -> httpx.Response:
        """Perform a request, retrying transient failures.

        Retries happen for connection errors, 5xx responses on idempotent
        requests (or when ``retry_safe`` is set) and for rate-limit responses,
        which are always safe to retry because the server rejected the request.
        """

        method = method.upper()
        attempt = 0
        while True:
            try:
                response = self._client.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderError(
                        f"{method} {path}: network error ({exc.__class__.__name__}): {exc}"
                    ) from exc
                self._sleep(self._backoff_delay(attempt))
                attempt += 1
                continue

            if self._should_retry(method, response, retry_safe=retry_safe, attempt=attempt):
                self._sleep(self._retry_delay(response, attempt))
                attempt += 1
                continue
            return response

    def _should_retry(
        self,
        method: str,
        response: httpx.Response,
        *,
        retry_safe: bool,
        attempt: int,
    ) -> bool:
        if attempt >= self._max_retries:
            return False
        status = response.status_code
        if status == 429:
            return True
        if status == 403 and is_rate_limited(response):
            return True
        return status in _RETRYABLE_STATUS and (retry_safe or method in _IDEMPOTENT_METHODS)

    def _backoff_delay(self, attempt: int) -> float:
        return min(self._backoff_base * (2**attempt), self._max_backoff)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(max(float(header), 0.0), self._max_backoff)
            except ValueError:
                try:
                    when = parsedate_to_datetime(header)
                except (TypeError, ValueError):
                    when = None
                if when is not None:
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    delay = (when - datetime.now(UTC)).total_seconds()
                    return min(max(delay, 0.0), self._max_backoff)
        return self._backoff_delay(attempt)


def is_rate_limited(response: httpx.Response) -> bool:
    """Detect provider rate-limit responses (GitHub and GitLab flavours)."""

    if response.status_code == 429:
        return True
    if response.status_code == 403:
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        if response.headers.get("retry-after"):
            return True
    return False


def extract_error_message(response: httpx.Response) -> str:
    """Extract a short, safe error message from a provider response body."""

    try:
        data = response.json()
    except ValueError:
        text = response.text.strip()
        return _truncate(text) if text else "no error details"
    if isinstance(data, dict):
        for key in ("message", "error", "error_description"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return _truncate(value)
        if isinstance(data.get("errors"), list) and data["errors"]:
            return _truncate(json.dumps(data["errors"], ensure_ascii=False))
    if isinstance(data, str) and data:
        return _truncate(data)
    return "no error details"


def raise_for_status(response: httpx.Response, *, context: str) -> None:
    """Map non-2xx responses to domain-specific errors."""

    status = response.status_code
    if status < 400:
        return
    message = extract_error_message(response)
    if status == 401:
        raise ProviderAuthenticationError(
            f"{context}: authentication failed (HTTP 401). Verify the API token and its scopes."
        )
    if is_rate_limited(response):
        reset = response.headers.get("x-ratelimit-reset")
        hint = f" (rate limit resets at epoch {reset})" if reset else ""
        raise ProviderRateLimitError(f"{context}: provider rate limit exceeded{hint}. Retry later.")
    if status == 403:
        raise ProviderAuthenticationError(f"{context}: access forbidden (HTTP 403). {message}")
    if status == 404:
        raise ResourceNotFoundError(f"{context}: resource not found (HTTP 404). {message}")
    raise ProviderError(f"{context}: request failed with HTTP {status}. {message}")


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with secrets replaced by ``***``."""

    return {
        name: "***" if name.lower() in _SECRET_HEADERS else value for name, value in headers.items()
    }


def _truncate(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= _ERROR_MESSAGE_LIMIT:
        return text
    return text[: _ERROR_MESSAGE_LIMIT - 3] + "..."
