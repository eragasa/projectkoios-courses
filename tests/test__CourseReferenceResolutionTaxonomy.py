from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_FIXTURE_ROOT = (
    Path(__file__).parent / "typing_fixtures" / "reference_resolution"
)
_REPOSITORY_ROOT = Path(__file__).parent.parent


def _run_fixture(tmp_path: Path, name: str) -> subprocess.CompletedProcess[str]:
    module = tmp_path / f"courses_reference_resolution_fixture_{name}.py"
    module.write_text(
        (_FIXTURE_ROOT / f"{name}.fixture").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(
        (
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(_REPOSITORY_ROOT / "pyproject.toml"),
            "--no-incremental",
            "--no-error-summary",
            str(module),
        ),
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test__mypy_taxonomy__accepts_declared_architecture_patterns(
    tmp_path: Path,
) -> None:
    result = _run_fixture(tmp_path, "allowed")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test__mypy_taxonomy__rejects_every_forbidden_architecture_shape(
    tmp_path: Path,
) -> None:
    result = _run_fixture(tmp_path, "forbidden")
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    for diagnostic in (
        "private cross-module import is forbidden",
        "private classes are forbidden",
        "nested classes are forbidden",
        "class lacks an allowed nominal taxonomy role",
        "data objects must be frozen and slotted",
        "data objects must be passive",
        "actionizers must expose exactly one action method",
        "private helpers must be top-level functions",
        "initializers must be docstring-only",
        "factories are forbidden",
    ):
        assert output.count(diagnostic) == 1, output
