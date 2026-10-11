"""Bounded, proxy-free HTTP transport for local proposal adapters."""

from __future__ import annotations

import ipaddress
import urllib.error
import urllib.request
from http.client import HTTPMessage
from types import TracebackType
from typing import IO, Any, NoReturn, Protocol, Self, cast
from urllib.parse import urlsplit

from .models import ProblemSolvingError

_CHUNK_BYTES = 8192


class ReadableResponse(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class LocalHttpResponse(ReadableResponse, Protocol):
    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _DenyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects without retaining the redirect response body."""

    @staticmethod
    def _deny(
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        headers: HTTPMessage,
    ) -> NoReturn:
        response.close()
        raise urllib.error.HTTPError(
            request.full_url,
            code,
            "Ollama redirects are forbidden",
            headers,
            None,
        )

    def http_error_301(
        self,
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
    ) -> Any | None:
        self._deny(request, response, code, headers)

    def http_error_302(
        self,
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
    ) -> Any | None:
        self._deny(request, response, code, headers)

    def http_error_303(
        self,
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
    ) -> Any | None:
        self._deny(request, response, code, headers)

    def http_error_307(
        self,
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
    ) -> Any | None:
        self._deny(request, response, code, headers)

    def http_error_308(
        self,
        request: urllib.request.Request,
        response: IO[bytes],
        code: int,
        message: str,
        headers: HTTPMessage,
    ) -> Any | None:
        self._deny(request, response, code, headers)


def _build_local_http_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _DenyRedirectHandler(),
    )


_LOCAL_HTTP_OPENER = _build_local_http_opener()


def open_local_request(
    request: urllib.request.Request,
    *,
    timeout: int,
) -> LocalHttpResponse:
    """Open through the dedicated proxy-free, redirect-denying transport."""
    return cast(
        LocalHttpResponse,
        _LOCAL_HTTP_OPENER.open(request, timeout=timeout),
    )


def validate_loopback_endpoint(endpoint: str) -> None:
    """Accept only an unauthenticated HTTP origin on numeric loopback."""
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise ProblemSolvingError("Ollama endpoint must be local") from error
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or getattr(address, "scope_id", None) is not None
        or port is None
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ProblemSolvingError("Ollama endpoint must be local")


def read_bounded_response(
    response: ReadableResponse,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    """Stream at most ``maximum_bytes`` into retained memory, then fail."""
    if maximum_bytes < 1:
        raise ProblemSolvingError("HTTP response byte bound must be positive")
    retained = bytearray()
    while True:
        remaining = maximum_bytes - len(retained)
        chunk = response.read(min(_CHUNK_BYTES, remaining + 1))
        if not isinstance(chunk, bytes):
            raise ProblemSolvingError(f"{label} returned a non-byte body")
        if not chunk:
            return bytes(retained)
        if len(chunk) > remaining:
            raise ProblemSolvingError(f"{label} exceeds its byte limit")
        retained.extend(chunk)
