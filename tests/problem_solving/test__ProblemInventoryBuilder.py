from __future__ import annotations

from dataclasses import replace

import pytest
from projectkoios.courses.problem_solving import (
    CorpusRole,
    InventoryConfiguration,
    PageLine,
    ProblemCategory,
    ProblemInventoryBuilder,
    ProblemMode,
    ProblemSolvingError,
)

SOURCE = "source:sha256:" + "a" * 64
FONT = "Eurostile-BoldCondensed"


def configuration() -> InventoryConfiguration:
    return InventoryConfiguration(
        book_id="Book",
        chapter=20,
        source_identity=SOURCE,
        anchor_font=FONT,
        anchor_font_size=9.0,
        anchor_font_size_tolerance=0.05,
        question_range=(1, 2),
        exercise_range=(1, 2),
        problem_range=(3, 3),
        challenge_range=(4, 4),
        tags=("CALC", "CP", "BIO"),
    )


def line(
    page: int,
    column: int,
    y: float,
    text: str,
    *,
    font: str = "Times-Roman",
) -> PageLine:
    return PageLine(page, column, y, text, font, 9.0)


def complete_lines() -> tuple[PageLine, ...]:
    return (
        line(10, 0, 20, "Questions"),
        line(10, 0, 100, "Q20.1 First conceptual question?", font=FONT),
        line(10, 0, 110, "Its continuation."),
        line(10, 1, 50, "Q20.2 BIO Second conceptual question?", font=FONT),
        line(11, 0, 10, "11 CHAPTER 20"),
        line(11, 0, 20, "Exercises"),
        line(11, 0, 30, "20.1 . First exercise.", font=FONT),
        line(11, 0, 40, "20.0°C is not a new exercise."),
        line(11, 0, 50, "20.2 .. CALC Second exercise.", font=FONT),
        line(11, 1, 20, "Problems"),
        line(11, 1, 30, "20.3 ... CP Third problem.", font=FONT),
        line(12, 0, 10, "Continuation on the next page."),
        line(12, 0, 20, "Challenge Problem"),
        line(12, 0, 30, "20.4 . CALC Fourth problem.", font=FONT),
    )


def test__build__creates_complete_role_separated_inventory() -> None:
    inventory = ProblemInventoryBuilder().build(
        complete_lines(),
        configuration(),
    )

    assert len(inventory.entries) == 6
    assert [entry.category for entry in inventory.entries] == [
        ProblemCategory.QUESTION,
        ProblemCategory.QUESTION,
        ProblemCategory.EXERCISE,
        ProblemCategory.EXERCISE,
        ProblemCategory.PROBLEM,
        ProblemCategory.CHALLENGE,
    ]
    assert inventory.by_id("Book.20.Q.01").statement == (
        "First conceptual question?\nIts continuation."
    )
    assert inventory.by_id("Book.20.P.03").source.physical_page_end == 12


def test__build__extracts_difficulty_and_tags() -> None:
    inventory = ProblemInventoryBuilder().build(
        complete_lines(),
        configuration(),
    )

    exercise = inventory.by_id("Book.20.P.02")
    assert exercise.difficulty == 2
    assert exercise.tags == ("CALC",)
    assert exercise.statement == "Second exercise."
    problem = inventory.by_id("Book.20.P.03")
    assert problem.difficulty == 3
    assert problem.tags == ("CP",)


def test__build__does_not_treat_body_number_as_anchor() -> None:
    inventory = ProblemInventoryBuilder().build(
        complete_lines(),
        configuration(),
    )

    assert "20.0°C" in inventory.by_id("Book.20.P.01").statement


def test__entry__converts_to_problem_material_without_changing_role() -> None:
    inventory = ProblemInventoryBuilder().build(
        complete_lines(),
        configuration(),
    )

    material = inventory.by_id("Book.20.P.02").as_problem_material(
        ProblemMode.ANALYTICAL_COMPUTATIONAL
    )

    assert material.corpus_role is CorpusRole.PROBLEM_MATERIAL
    assert material.statement == "Second exercise."


def test__build__rejects_missing_inventory_identity() -> None:
    incomplete = tuple(
        item for item in complete_lines() if not item.text.startswith("20.4")
    )

    with pytest.raises(
        ProblemSolvingError,
        match="challenge_problem inventory is incomplete",
    ):
        ProblemInventoryBuilder().build(incomplete, configuration())


def test__configuration__rejects_invalid_number_range() -> None:
    with pytest.raises(ProblemSolvingError, match="number range"):
        replace(configuration(), challenge_range=(5, 4))
