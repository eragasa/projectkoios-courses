"""Deterministic, read-only course-review candidate projection."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .identities import validate_nominal_identity

_COURSE_CODE_RE = re.compile(r"[A-Z0-9-]{2,32}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class CourseDomainError(ValueError):
    """Raised when a course-domain input violates its contract."""


class CourseMaterialsStatus(StrEnum):
    INVENTORY_ONLY = "inventory-only"
    REVIEW_CANDIDATE = "review-candidate"
    PUBLISHED = "published"


class CourseMatchField(StrEnum):
    RELATIVE_PATH = "relative_path"
    SUGGESTED_GROUP = "suggested_group"


class CourseReviewAuthority(StrEnum):
    CANDIDATE_EVIDENCE_ONLY = "candidate_evidence_only"


class CourseCandidateReviewStatus(StrEnum):
    AUTOMATED_UNREVIEWED = "AUTOMATED_UNREVIEWED"


@dataclass(frozen=True)
class CourseRecord:
    """Canonical identity and display metadata for one course."""

    course_id: str
    institution_id: str
    institution_name: str
    code: str
    title: str | None

    def __post_init__(self) -> None:
        _validate_identity(self.course_id, label="course identity")
        _validate_identity(
            self.institution_id,
            label="institution identity",
        )
        if self.course_id != self.course_id.lower():
            raise CourseDomainError("course identity must be lowercase")
        if self.institution_id != self.institution_id.lower():
            raise CourseDomainError("institution identity must be lowercase")
        if not self.course_id.startswith(f"{self.institution_id}."):
            raise CourseDomainError(
                "course identity must use its institution prefix"
            )
        if _COURSE_CODE_RE.fullmatch(self.code) is None:
            raise CourseDomainError("course code is invalid")
        _validate_text(
            self.institution_name,
            label="institution name",
            maximum_characters=200,
        )
        if self.title is not None:
            _validate_text(
                self.title,
                label="course title",
                maximum_characters=240,
            )


@dataclass(frozen=True)
class CourseMaterial:
    """Canonical material-review state attached to one course record."""

    course_id: str
    status: CourseMaterialsStatus

    def __post_init__(self) -> None:
        _validate_identity(self.course_id, label="course identity")
        if not isinstance(self.status, CourseMaterialsStatus):
            raise CourseDomainError("course material status must be explicit")


@dataclass(frozen=True)
class CourseProposalProjection:
    """Sanitized metadata projection used for matching; never file content."""

    proposal_id: str
    relative_path: str
    suggested_group: str
    life_domain: str = "teaching"

    def __post_init__(self) -> None:
        _validate_identity(self.proposal_id, label="proposal identity")
        if self.life_domain != "teaching":
            raise CourseDomainError(
                "course review accepts teaching proposals only"
            )
        _validate_relative_path(self.relative_path)
        _validate_text(
            self.suggested_group,
            label="suggested group",
            maximum_characters=120,
        )


@dataclass(frozen=True)
class CourseMatchEvidence:
    """Token-match evidence for exactly one proposal/course pair."""

    proposal_id: str
    course_id: str
    matched_fields: tuple[CourseMatchField, ...]

    def __post_init__(self) -> None:
        _validate_identity(self.proposal_id, label="proposal identity")
        _validate_identity(self.course_id, label="course identity")
        expected_order = tuple(
            field for field in CourseMatchField if field in self.matched_fields
        )
        if not self.matched_fields or self.matched_fields != expected_order:
            raise CourseDomainError(
                "course match fields must be unique and canonical"
            )


@dataclass(frozen=True)
class CourseReviewCandidate:
    """Non-authoritative review candidate for one canonical course."""

    course: CourseRecord
    material: CourseMaterial
    match_evidence: tuple[CourseMatchEvidence, ...]
    authority: CourseReviewAuthority = (
        CourseReviewAuthority.CANDIDATE_EVIDENCE_ONLY
    )
    review_status: CourseCandidateReviewStatus = (
        CourseCandidateReviewStatus.AUTOMATED_UNREVIEWED
    )

    def __post_init__(self) -> None:
        if self.material.course_id != self.course.course_id:
            raise CourseDomainError(
                "course material targets a different course"
            )
        if any(
            evidence.course_id != self.course.course_id
            for evidence in self.match_evidence
        ):
            raise CourseDomainError(
                "course match evidence targets a different course"
            )
        proposal_ids = tuple(
            evidence.proposal_id for evidence in self.match_evidence
        )
        if proposal_ids != tuple(sorted(set(proposal_ids))):
            raise CourseDomainError(
                "candidate match evidence must be unique and ordered"
            )
        if self.authority is not CourseReviewAuthority.CANDIDATE_EVIDENCE_ONLY:
            raise CourseDomainError("course review authority cannot be widened")
        if (
            self.review_status
            is not CourseCandidateReviewStatus.AUTOMATED_UNREVIEWED
        ):
            raise CourseDomainError(
                "course review candidates must remain unreviewed"
            )


@dataclass(frozen=True)
class CourseMatchAmbiguity:
    """One proposal with token evidence for multiple canonical courses."""

    proposal: CourseProposalProjection
    evidence: tuple[CourseMatchEvidence, ...]

    def __post_init__(self) -> None:
        course_ids = tuple(item.course_id for item in self.evidence)
        if (
            len(course_ids) < 2
            or course_ids != tuple(sorted(set(course_ids)))
            or any(
                item.proposal_id != self.proposal.proposal_id
                for item in self.evidence
            )
        ):
            raise CourseDomainError(
                "ambiguous course evidence must name multiple ordered courses"
            )


@dataclass(frozen=True)
class CourseReviewProjection:
    """Complete deterministic result, including unresolved proposal outcomes."""

    candidates: tuple[CourseReviewCandidate, ...]
    ambiguous: tuple[CourseMatchAmbiguity, ...]
    unmatched: tuple[CourseProposalProjection, ...]


class CourseReviewProjector:
    """Project course-code token evidence without decisions or side effects."""

    def project(
        self,
        courses: tuple[CourseRecord, ...],
        materials: tuple[CourseMaterial, ...],
        proposals: tuple[CourseProposalProjection, ...],
    ) -> CourseReviewProjection:
        courses_by_id = _unique_by_id(
            courses,
            identity=lambda item: item.course_id,
            label="course",
        )
        materials_by_id = _unique_by_id(
            materials,
            identity=lambda item: item.course_id,
            label="course material",
        )
        _unique_by_id(
            proposals,
            identity=lambda item: item.proposal_id,
            label="proposal",
        )
        if set(courses_by_id) != set(materials_by_id):
            raise CourseDomainError(
                "every course must have exactly one material record"
            )

        evidence_by_course: dict[str, list[CourseMatchEvidence]] = {
            course_id: [] for course_id in courses_by_id
        }
        ambiguous: list[CourseMatchAmbiguity] = []
        unmatched: list[CourseProposalProjection] = []

        for proposal in sorted(
            proposals,
            key=lambda item: item.proposal_id,
        ):
            matches = tuple(
                evidence
                for course in sorted(
                    courses,
                    key=lambda item: item.course_id,
                )
                if (evidence := _match_evidence(course, proposal)) is not None
            )
            if not matches:
                unmatched.append(proposal)
            elif len(matches) > 1:
                ambiguous.append(
                    CourseMatchAmbiguity(
                        proposal=proposal,
                        evidence=matches,
                    )
                )
            else:
                evidence_by_course[matches[0].course_id].append(matches[0])

        candidates = tuple(
            CourseReviewCandidate(
                course=courses_by_id[course_id],
                material=materials_by_id[course_id],
                match_evidence=tuple(evidence_by_course[course_id]),
            )
            for course_id in sorted(courses_by_id)
            if evidence_by_course[course_id]
            or materials_by_id[course_id].status
            is CourseMaterialsStatus.REVIEW_CANDIDATE
        )
        return CourseReviewProjection(
            candidates=candidates,
            ambiguous=tuple(ambiguous),
            unmatched=tuple(unmatched),
        )


def _match_evidence(
    course: CourseRecord,
    proposal: CourseProposalProjection,
) -> CourseMatchEvidence | None:
    token = re.compile(
        rf"(^|[^A-Z0-9]){re.escape(course.code)}([^A-Z0-9]|$)",
        flags=re.IGNORECASE | re.ASCII,
    )
    fields = tuple(
        field
        for field, value in (
            (CourseMatchField.RELATIVE_PATH, proposal.relative_path),
            (CourseMatchField.SUGGESTED_GROUP, proposal.suggested_group),
        )
        if token.search(value) is not None
    )
    if not fields:
        return None
    return CourseMatchEvidence(
        proposal_id=proposal.proposal_id,
        course_id=course.course_id,
        matched_fields=fields,
    )


def _unique_by_id[T](
    values: tuple[T, ...],
    *,
    identity: Callable[[T], str],
    label: str,
) -> dict[str, T]:
    result: dict[str, T] = {}
    for value in values:
        item_id = identity(value)
        if item_id in result:
            raise CourseDomainError(f"{label} identities must be unique")
        result[item_id] = value
    return result


def _validate_identity(value: str, *, label: str) -> None:
    try:
        validate_nominal_identity(value, label=label)
    except ValueError as error:
        raise CourseDomainError(str(error)) from error


def _validate_text(
    value: str,
    *,
    label: str,
    maximum_characters: int,
) -> None:
    if (
        not value.strip()
        or len(value) > maximum_characters
        or _CONTROL_RE.search(value)
    ):
        raise CourseDomainError(f"{label} is invalid")


def _validate_relative_path(value: str) -> None:
    if (
        not value
        or len(value.encode("utf-8")) > 4096
        or _CONTROL_RE.search(value)
        or "\\" in value
    ):
        raise CourseDomainError("proposal relative path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise CourseDomainError("proposal relative path is invalid")
