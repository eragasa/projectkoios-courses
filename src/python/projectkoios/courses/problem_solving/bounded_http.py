"""Bounded HTTP response-body reads for local proposal adapters."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

from .models import ProblemSolvingError

_CHUNK_BYTES = 8192


class ReadableResponse(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


def validate_loopback_endpoint(endpoint: str) -> None:
    """Accept only an unauthenticated HTTP origin on IPv4 loopback."""
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as error:
        raise ProblemSolvingError("Ollama endpoint must be local") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
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
