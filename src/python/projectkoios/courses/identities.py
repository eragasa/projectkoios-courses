"""Shared validation for nominal course-domain identities."""

from __future__ import annotations

import re

_NOMINAL_IDENTITY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_-]|\.(?=[A-Za-z0-9])){0,127}"
)


def validate_nominal_identity(value: str, *, label: str) -> None:
    """Require a bounded ASCII name, never a path or path alias."""
    if _NOMINAL_IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded ASCII nominal identity")
