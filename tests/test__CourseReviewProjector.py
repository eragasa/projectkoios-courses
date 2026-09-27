from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from projectkoios.courses import (
    CourseCandidateReviewStatus,
    CourseMatchEvidence,
    CourseMatchField,
    CourseMaterial,
    CourseMaterialsStatus,
    CourseProposalProjection,
    CourseRecord,
    CourseReviewAuthority,
    CourseReviewProjector,
)


def courses() -> tuple[CourseRecord, ...]:
    return (
        CourseRecord(
            course_id="pacific.engr219",
            institution_id="pacific",
            institution_name="Example Pacific University",
            code="ENGR219",
            title="Numerical Methods for Engineering",
        ),
        CourseRecord(
            course_id="example.engr219",
            institution_id="example",
            institution_name="Example Institute",
            code="ENGR219",
            title="Engineering Seminar",
        ),
        CourseRecord(
            course_id="pacific.math157",
            institution_id="pacific",
            institution_name="Example Pacific University",
            code="MATH157",
            title="Applied Differential Equations II",
        ),
    )


def materials() -> tuple[CourseMaterial, ...]:
    return (
        CourseMaterial(
            course_id="pacific.engr219",
            status=CourseMaterialsStatus.REVIEW_CANDIDATE,
        ),
        CourseMaterial(
            course_id="example.engr219",
            status=CourseMaterialsStatus.INVENTORY_ONLY,
        ),
        CourseMaterial(
            course_id="pacific.math157",
            status=CourseMaterialsStatus.INVENTORY_ONLY,
        ),
    )


def proposals() -> tuple[CourseProposalProjection, ...]:
    return (
        CourseProposalProjection(
            proposal_id="1" * 64,
            relative_path="Courses/ENGR219/Homework/example.m",
            suggested_group="ENGR219",
        ),
        CourseProposalProjection(
            proposal_id="2" * 64,
            relative_path="Teaching/differential-equations/notes.txt",
            suggested_group="MATH157 materials",
        ),
        CourseProposalProjection(
            proposal_id="3" * 64,
            relative_path="Teaching/uncategorized/notes.txt",
            suggested_group="Course notes",
        ),
        CourseProposalProjection(
            proposal_id="4" * 64,
            relative_path="Teaching/XMATH157Y/notes.txt",
            suggested_group="Unresolved",
        ),
    )


def test__project__emits_candidate_only_evidence_and_unmatched_outcomes() -> (
    None
):
    projection = CourseReviewProjector().project(
        courses(),
        materials(),
        proposals(),
    )

    assert [item.course.course_id for item in projection.candidates] == [
        "pacific.engr219",
        "pacific.math157",
    ]
    review_only, matched = projection.candidates
    assert review_only.match_evidence == ()
    assert review_only.authority is (
        CourseReviewAuthority.CANDIDATE_EVIDENCE_ONLY
    )
    assert review_only.review_status is (
        CourseCandidateReviewStatus.AUTOMATED_UNREVIEWED
    )
    assert matched.match_evidence[0].proposal_id == "2" * 64
    assert matched.match_evidence[0].matched_fields == (
        CourseMatchField.SUGGESTED_GROUP,
    )
    assert [item.proposal_id for item in projection.unmatched] == [
        "3" * 64,
        "4" * 64,
    ]


def test__project__isolates_ambiguous_code_match() -> None:
    projection = CourseReviewProjector().project(
        courses(),
        materials(),
        proposals(),
    )

    assert len(projection.ambiguous) == 1
    ambiguity = projection.ambiguous[0]
    assert ambiguity.proposal.proposal_id == "1" * 64
    assert [item.course_id for item in ambiguity.evidence] == [
        "example.engr219",
        "pacific.engr219",
    ]
    assert all(
        item.matched_fields
        == (
            CourseMatchField.RELATIVE_PATH,
            CourseMatchField.SUGGESTED_GROUP,
        )
        for item in ambiguity.evidence
    )
    assert all(
        evidence.proposal_id != "1" * 64
        for candidate in projection.candidates
        for evidence in candidate.match_evidence
    )


def test__project__emits_exact_nominal_match_fields() -> None:
    projection = CourseReviewProjector().project(
        courses(),
        materials(),
        proposals(),
    )

    assert all(
        type(field) is CourseMatchField
        for candidate in projection.candidates
        for evidence in candidate.match_evidence
        for field in evidence.matched_fields
    )


def test__project__is_deterministic_across_input_order() -> None:
    projector = CourseReviewProjector()

    forward = projector.project(courses(), materials(), proposals())
    reversed_inputs = projector.project(
        tuple(reversed(courses())),
        tuple(reversed(materials())),
        tuple(reversed(proposals())),
    )

    assert reversed_inputs == forward


@pytest.mark.parametrize(
    "code",
    ["A-1", "BIO-ENG-101", "ENGR219"],
)
def test__course_record__accepts_deliberate_single_hyphens(code: str) -> None:
    record = CourseRecord(
        course_id="example.course",
        institution_id="example",
        institution_name="Example Institute",
        code=code,
        title=None,
    )

    assert record.code == code


@pytest.mark.parametrize(
    "code",
    ["--", "-A1", "A1-", "A--1", "A---1", "A_1"],
)
def test__course_record__rejects_edge_or_repeated_separators(
    code: str,
) -> None:
    with pytest.raises(ValueError, match="course code is invalid"):
        CourseRecord(
            course_id="example.course",
            institution_id="example",
            institution_name="Example Institute",
            code=code,
            title=None,
        )


def test__match_evidence__rejects_string_impersonation_of_enum() -> None:
    with pytest.raises(ValueError, match="CourseMatchField values"):
        CourseMatchEvidence(
            proposal_id="a" * 64,
            course_id="example.course",
            matched_fields=("relative_path",),  # type: ignore[arg-type]
        )


def test__review_candidate_is_frozen() -> None:
    candidate = (
        CourseReviewProjector()
        .project(courses(), materials(), proposals())
        .candidates[0]
    )

    with pytest.raises(FrozenInstanceError):
        candidate.review_status = (  # type: ignore[misc]
            CourseCandidateReviewStatus.AUTOMATED_UNREVIEWED
        )


@pytest.mark.parametrize(
    "relative_path",
    ["/absolute/file.txt", "../escape.txt", "safe/../alias.txt"],
)
def test__proposal_projection__rejects_non_relative_aliases(
    relative_path: str,
) -> None:
    with pytest.raises(ValueError, match="relative path"):
        CourseProposalProjection(
            proposal_id="a" * 64,
            relative_path=relative_path,
            suggested_group="Unresolved",
        )
