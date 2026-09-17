from __future__ import annotations

import projectkoios.courses


def test__package__imports() -> None:
    assert projectkoios.courses.__name__ == "projectkoios.courses"
