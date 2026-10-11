from __future__ import annotations

from pathlib import Path

import pytest
from projectkoios.courses.problem_solving import (
    ConversionIntentProblem,
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    SourceSpan,
    text_sha256,
)


@pytest.mark.parametrize(
    "problem_id",
    [
        "../escape",
        "/absolute",
        "C:/absolute",
        "book/problem",
        "book\\problem",
        "prøblem.1",
        "problem.\x00.1",
        ".",
        "..",
        "book..problem",
        "book.",
        "a" * 129,
    ],
    ids=[
        "traversal",
        "posix-absolute",
        "drive-like-absolute",
        "forward-separator",
        "back-separator",
        "unicode",
        "control",
        "current-alias",
        "parent-alias",
        "empty-segment-alias",
        "trailing-segment-alias",
        "over-bound",
    ],
)
def test__problem_material__rejects_non_nominal_identity(
    problem_id: str,
) -> None:
    statement = "Sanitized fixture statement."

    with pytest.raises(ProblemSolvingError, match="nominal identity"):
        ProblemMaterial(
            problem_id=problem_id,
            statement=statement,
            statement_sha256=text_sha256(statement),
            mode=ProblemMode.CONCEPTUAL,
            source=SourceSpan("source:sha256:" + "a" * 64, 1, 1),
        )


def test__conversion_problem__uses_same_nominal_identity_contract() -> None:
    statement = "Convert 55.0 miles per hour."

    with pytest.raises(ProblemSolvingError, match="nominal identity"):
        ConversionIntentProblem(
            problem_id="../escape",
            statement=statement,
            statement_sha256=text_sha256(statement),
            candidate_given_lexeme="55.0",
        )


def test__safe_problem_identity_is_not_interpreted_as_archive_path() -> None:
    statement = "Sanitized fixture statement."
    material = ProblemMaterial(
        problem_id="book.20.Q.01",
        statement=statement,
        statement_sha256=text_sha256(statement),
        mode=ProblemMode.CONCEPTUAL,
        source=SourceSpan("source:sha256:" + "a" * 64, 1, 1),
    )

    identity_path = Path(material.problem_id)
    assert identity_path.name == material.problem_id
    assert not identity_path.is_absolute()
