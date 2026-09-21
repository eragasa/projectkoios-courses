from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .models import (
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    SourceSpan,
    text_sha256,
)


class ProblemCategory(StrEnum):
    QUESTION = "question"
    EXERCISE = "exercise"
    PROBLEM = "problem"
    CHALLENGE = "challenge_problem"


@dataclass(frozen=True)
class PageLine:
    physical_pdf_page: int
    column: int
    vertical_position: float
    text: str
    first_font: str
    first_font_size: float

    def __post_init__(self) -> None:
        if self.physical_pdf_page < 1 or self.column < 0:
            raise ProblemSolvingError("page line position is invalid")
        if not self.text.strip() or not self.first_font:
            raise ProblemSolvingError("page line text and font are required")


@dataclass(frozen=True)
class InventoryConfiguration:
    book_id: str
    chapter: int
    source_identity: str
    anchor_font: str
    anchor_font_size: float
    anchor_font_size_tolerance: float
    question_range: tuple[int, int]
    exercise_range: tuple[int, int]
    problem_range: tuple[int, int]
    challenge_range: tuple[int, int]
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.book_id or self.chapter < 1:
            raise ProblemSolvingError("book and chapter identity are required")
        SourceSpan(self.source_identity, 1, 1)
        if self.anchor_font_size <= 0 or self.anchor_font_size_tolerance < 0:
            raise ProblemSolvingError("anchor font configuration is invalid")
        ranges = (
            self.question_range,
            self.exercise_range,
            self.problem_range,
            self.challenge_range,
        )
        if any(start < 1 or end < start for start, end in ranges):
            raise ProblemSolvingError("problem number range is invalid")
        if len(set(self.tags)) != len(self.tags):
            raise ProblemSolvingError("configured problem tags must be unique")


@dataclass(frozen=True)
class ProblemInventoryEntry:
    problem_id: str
    source_label: str
    category: ProblemCategory
    number: int
    difficulty: int | None
    tags: tuple[str, ...]
    statement: str
    statement_sha256: str
    source: SourceSpan

    def __post_init__(self) -> None:
        if not self.problem_id or not self.source_label:
            raise ProblemSolvingError("problem inventory identity is required")
        if not self.statement.strip():
            raise ProblemSolvingError("problem statement is empty")
        if self.statement_sha256 != text_sha256(self.statement):
            raise ProblemSolvingError("inventory statement identity mismatch")

    def as_problem_material(self, mode: ProblemMode) -> ProblemMaterial:
        return ProblemMaterial(
            problem_id=self.problem_id,
            statement=self.statement,
            statement_sha256=self.statement_sha256,
            mode=mode,
            source=self.source,
        )


@dataclass(frozen=True)
class ProblemInventory:
    entries: tuple[ProblemInventoryEntry, ...]

    def by_id(self, problem_id: str) -> ProblemInventoryEntry:
        matches = [
            entry for entry in self.entries if entry.problem_id == problem_id
        ]
        if len(matches) != 1:
            raise ProblemSolvingError(
                f"problem inventory identity is not unique: {problem_id}"
            )
        return matches[0]


@dataclass(frozen=True)
class _Anchor:
    source_label: str
    category: ProblemCategory
    number: int
    difficulty: int | None
    tags: tuple[str, ...]
    first_statement_line: str


class ProblemInventoryBuilder:
    _QUESTION = re.compile(
        r"^Q(?P<chapter>\d+)\.(?P<number>\d+)\s+(?P<body>.+)$"
    )
    _NUMBERED = re.compile(
        r"^(?P<chapter>\d+)\.(?P<number>\d+)\s+"
        r"(?P<difficulty>\.{1,3})\s*(?P<body>.+)$"
    )
    _IGNORED = (
        re.compile(r"^\d+\s+CHAPTER\s+\d+"),
        re.compile(r"^CHAPTER\s+\d+"),
        re.compile(r"^(?:Questions|Exercises|Problems|Challenge Problem)$"),
        re.compile(r"^Section\s+\d+\.\d+"),
        re.compile(r"^\d+$"),
    )

    def build(
        self,
        lines: tuple[PageLine, ...],
        configuration: InventoryConfiguration,
    ) -> ProblemInventory:
        ordered = tuple(
            sorted(
                lines,
                key=lambda line: (
                    line.physical_pdf_page,
                    line.column,
                    line.vertical_position,
                ),
            )
        )
        entries: list[ProblemInventoryEntry] = []
        current_anchor: _Anchor | None = None
        current_lines: list[PageLine] = []

        for line in ordered:
            anchor = self._anchor(line, configuration)
            if anchor is not None:
                if current_anchor is not None:
                    entries.append(
                        self._entry(
                            current_anchor,
                            tuple(current_lines),
                            configuration,
                        )
                    )
                current_anchor = anchor
                current_lines = [
                    PageLine(
                        physical_pdf_page=line.physical_pdf_page,
                        column=line.column,
                        vertical_position=line.vertical_position,
                        text=anchor.first_statement_line,
                        first_font=line.first_font,
                        first_font_size=line.first_font_size,
                    )
                ]
                continue
            if current_anchor is not None and not self._ignored(line.text):
                current_lines.append(line)

        if current_anchor is not None:
            entries.append(
                self._entry(
                    current_anchor,
                    tuple(current_lines),
                    configuration,
                )
            )
        inventory = ProblemInventory(tuple(entries))
        self._validate_inventory(inventory, configuration)
        return inventory

    def _anchor(
        self,
        line: PageLine,
        configuration: InventoryConfiguration,
    ) -> _Anchor | None:
        if line.first_font != configuration.anchor_font:
            return None
        if (
            abs(line.first_font_size - configuration.anchor_font_size)
            > configuration.anchor_font_size_tolerance
        ):
            return None

        question = self._QUESTION.match(line.text)
        if question is not None:
            chapter = int(question.group("chapter"))
            number = int(question.group("number"))
            if chapter != configuration.chapter:
                return None
            start, end = configuration.question_range
            if not start <= number <= end:
                return None
            body, tags = self._extract_tags(
                question.group("body"),
                configuration.tags,
            )
            return _Anchor(
                source_label=f"Q{chapter}.{number}",
                category=ProblemCategory.QUESTION,
                number=number,
                difficulty=None,
                tags=tags,
                first_statement_line=body,
            )

        numbered = self._NUMBERED.match(line.text)
        if numbered is None:
            return None
        chapter = int(numbered.group("chapter"))
        number = int(numbered.group("number"))
        if chapter != configuration.chapter:
            return None
        category = self._category(number, configuration)
        if category is None:
            return None
        body, tags = self._extract_tags(
            numbered.group("body"),
            configuration.tags,
        )
        return _Anchor(
            source_label=f"{chapter}.{number}",
            category=category,
            number=number,
            difficulty=len(numbered.group("difficulty")),
            tags=tags,
            first_statement_line=body,
        )

    @staticmethod
    def _category(
        number: int,
        configuration: InventoryConfiguration,
    ) -> ProblemCategory | None:
        ranges = (
            (configuration.exercise_range, ProblemCategory.EXERCISE),
            (configuration.problem_range, ProblemCategory.PROBLEM),
            (configuration.challenge_range, ProblemCategory.CHALLENGE),
        )
        for (start, end), category in ranges:
            if start <= number <= end:
                return category
        return None

    @staticmethod
    def _extract_tags(
        body: str,
        configured_tags: tuple[str, ...],
    ) -> tuple[str, tuple[str, ...]]:
        remaining = body.strip()
        tags: list[str] = []
        while remaining:
            token, separator, rest = remaining.partition(" ")
            if token not in configured_tags:
                break
            tags.append(token)
            remaining = rest.strip() if separator else ""
        return remaining, tuple(tags)

    @classmethod
    def _ignored(cls, text: str) -> bool:
        stripped = text.strip()
        return any(pattern.match(stripped) for pattern in cls._IGNORED)

    @staticmethod
    def _entry(
        anchor: _Anchor,
        lines: tuple[PageLine, ...],
        configuration: InventoryConfiguration,
    ) -> ProblemInventoryEntry:
        statement = "\n".join(line.text.strip() for line in lines).strip()
        pages = tuple(line.physical_pdf_page for line in lines)
        prefix = "Q" if anchor.category is ProblemCategory.QUESTION else "P"
        problem_id = (
            f"{configuration.book_id}.{configuration.chapter:02d}."
            f"{prefix}.{anchor.number:02d}"
        )
        return ProblemInventoryEntry(
            problem_id=problem_id,
            source_label=anchor.source_label,
            category=anchor.category,
            number=anchor.number,
            difficulty=anchor.difficulty,
            tags=anchor.tags,
            statement=statement,
            statement_sha256=text_sha256(statement),
            source=SourceSpan(
                configuration.source_identity,
                min(pages),
                max(pages),
            ),
        )

    @staticmethod
    def _validate_inventory(
        inventory: ProblemInventory,
        configuration: InventoryConfiguration,
    ) -> None:
        expected = {
            ProblemCategory.QUESTION: configuration.question_range,
            ProblemCategory.EXERCISE: configuration.exercise_range,
            ProblemCategory.PROBLEM: configuration.problem_range,
            ProblemCategory.CHALLENGE: configuration.challenge_range,
        }
        for category, (start, end) in expected.items():
            numbers = tuple(
                entry.number
                for entry in inventory.entries
                if entry.category is category
            )
            if numbers != tuple(range(start, end + 1)):
                raise ProblemSolvingError(
                    f"{category} inventory is incomplete or unordered"
                )
        identities = tuple(entry.problem_id for entry in inventory.entries)
        if len(set(identities)) != len(identities):
            raise ProblemSolvingError(
                "problem inventory identities are not unique"
            )
