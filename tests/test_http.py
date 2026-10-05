"""Tests for the shared HTTP client: retries, backoff, error mapping, redaction."""

from __future__ import annotations

import httpx
import pytest

from repo_planner.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ResourceNotFoundError,
)
from repo_planner.http import (
    HttpClient,
    extract_error_message,
    raise_for_status,
    redact_headers,
)


def make_client(handler, *, max_retries: int = 3) -> tuple[HttpClient, list[float]]:
    sleeps: list[float] = []
    client = HttpClient(
        "https://api.example.com",
        transport=httpx.MockTransport(handler),
        max_retries=max_retries,
        sleep=sleeps.append,
    )
    return client, sleeps


def test_retries_transient_errors_for_get() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"ok": True})

    client, sleeps = make_client(handler)
    response = client.request("GET", "/x")
    assert response.status_code == 200
    assert len(attempts) == 3
    assert sleeps == [0.5, 1.0]


def test_no_retry_for_post_on_server_error() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500)

    client, _ = make_client(handler)
    response = client.request("POST", "/x")
    assert response.status_code == 500
    assert len(attempts) == 1


def test_retry_after_header_is_honored() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200)

    client, sleeps = make_client(handler)
    assert client.request("GET", "/x").status_code == 200
    assert sleeps == [7.0]


def test_rate_limited_post_is_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(201)

    client, _ = make_client(handler)
    assert client.request("POST", "/x", json={"a": 1}).status_code == 201
    assert len(attempts) == 2


def test_transport_errors_raise_provider_error_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client, sleeps = make_client(handler, max_retries=2)
    with pytest.raises(ProviderError) as excinfo:
        client.request("GET", "/x")
    assert "network error" in str(excinfo.value)
    assert len(sleeps) == 2


def test_raise_for_status_mapping() -> None:
    with pytest.raises(ProviderAuthenticationError):
        raise_for_status(httpx.Response(401, json={"message": "bad"}), context="GET /x")
    with pytest.raises(ProviderAuthenticationError):
        raise_for_status(httpx.Response(403, json={"message": "forbidden"}), context="GET /x")
    with pytest.raises(ResourceNotFoundError):
        raise_for_status(httpx.Response(404, json={"message": "gone"}), context="GET /x")
    with pytest.raises(ProviderError) as excinfo:
        raise_for_status(httpx.Response(422, json={"message": "invalid"}), context="GET /x")
    assert "invalid" in str(excinfo.value)
    with pytest.raises(ProviderRateLimitError):
        raise_for_status(
            httpx.Response(403, headers={"x-ratelimit-remaining": "0"}),
            context="GET /x",
        )
    with pytest.raises(ProviderRateLimitError):
        raise_for_status(httpx.Response(429, headers={"retry-after": "30"}), context="GET /x")


def test_2xx_does_not_raise() -> None:
    raise_for_status(httpx.Response(200), context="GET /x")
    raise_for_status(httpx.Response(204), context="GET /x")


def test_redact_headers() -> None:
    redacted = redact_headers(
        {"Authorization": "Bearer secret", "PRIVATE-TOKEN": "secret", "Accept": "json"}
    )
    assert redacted == {"Authorization": "***", "PRIVATE-TOKEN": "***", "Accept": "json"}


def test_extract_error_message() -> None:
    assert (
        extract_error_message(httpx.Response(400, json={"message": "bad request"})) == "bad request"
    )
    assert extract_error_message(httpx.Response(400, text="plain text")) == "plain text"
    assert extract_error_message(httpx.Response(400)) == "no error details"
