from __future__ import annotations

import io
import urllib.error
import urllib.request
from http.client import HTTPMessage
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving.bounded_http import (
    _build_local_http_opener,
    _DenyRedirectHandler,
    validate_loopback_endpoint,
)
from projectkoios.courses.problem_solving.models import ProblemSolvingError


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:1",
        "http://127.255.255.254:65535/",
        "http://[::1]:11434",
    ],
)
def test__validate_loopback_endpoint__accepts_numeric_loopback_edges(
    endpoint: str,
) -> None:
    validate_loopback_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://localhost:11434",
        "http://127.1:11434",
        "http://2130706433:11434",
        "http://127.0.0.1:11434@example.invalid",
        "http://user@127.0.0.1:11434",
        "http://[::2]:11434",
        "http://[::1%25lo0]:11434",
        "http://0.0.0.0:11434",
        "https://127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
    ],
)
def test__validate_loopback_endpoint__rejects_ambiguous_or_nonlocal_origins(
    endpoint: str,
) -> None:
    with pytest.raises(ProblemSolvingError, match="endpoint must be local"):
        validate_loopback_endpoint(endpoint)


def test__local_http_opener__ignores_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:9999")
    monkeypatch.setenv("NO_PROXY", "")

    with patch(
        "urllib.request.getproxies",
        side_effect=AssertionError("proxy environment was consulted"),
    ):
        opener = _build_local_http_opener()

    assert isinstance(opener, urllib.request.OpenerDirector)


def test__redirect_handler__rejects_nonloopback_target_and_closes_body() -> (
    None
):
    body = io.BytesIO(b"redirect response")
    headers = HTTPMessage()
    headers["Location"] = "http://192.0.2.1/escaped"
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags")

    with pytest.raises(urllib.error.HTTPError, match="redirects are forbidden"):
        _DenyRedirectHandler().http_error_302(
            request,
            body,
            302,
            "Found",
            headers,
        )

    assert body.closed
