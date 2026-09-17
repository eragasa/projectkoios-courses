from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest
from projectkoios.courses.sanitization import (
    CourseSanitizer,
    ExclusionRule,
    LiteralRedaction,
    ProvenanceEntry,
    SanitizationError,
    SanitizationPolicy,
)


def policy() -> SanitizationPolicy:
    return SanitizationPolicy(
        institution="Example University",
        course_code="TEST101",
        course_title="Testing",
        source_alias="example/TEST101",
        retained_text=("Homework/**/*.txt",),
        retained_pdfs=(),
        exclusions=(
            ExclusionRule("Books/**", "third-party textbook"),
        ),
        literal_redactions=(
            LiteralRedaction("student name", "Test Student"),
        ),
    )


def test__sanitize__retains_allowlisted_text_and_audits_exclusions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "Homework/week-01").mkdir(parents=True)
    (source / "Books").mkdir()
    (source / "Homework/week-01/Test Student-answer.txt").write_text(
        "Test Student test@example.com student ID: 1234567\n",
        encoding="utf-8",
    )
    (source / "Books/textbook-1234567.pdf").write_bytes(b"third party")
    destination = tmp_path / "legacy"

    result = CourseSanitizer().sanitize(source, destination, policy())

    retained = destination / (
        "student-work/Homework/week-01/"
        "[REDACTED-STUDENT-NAME]-answer.txt"
    )
    assert retained.read_text(encoding="utf-8") == (
        "[REDACTED STUDENT NAME] [REDACTED EMAIL ADDRESS] "
        "student ID: [REDACTED]\n"
    )
    assert result.reviewed == 2
    assert result.retained == 1
    assert result.excluded == 1
    assert result.redactions == 3
    assert not (destination / "Books/textbook-1234567.pdf").exists()

    rows = list(
        csv.DictReader(
            (destination / "audit/classification.csv").open(
                encoding="utf-8"
            )
        )
    )
    assert {row["action"] for row in rows} == {
        "excluded",
        "retained-sanitized",
    }
    excluded = next(row for row in rows if row["action"] == "excluded")
    assert excluded["reason"] == "third-party textbook"
    assert all(not row["source_path"].startswith("/") for row in rows)
    assert "Test Student" not in json.dumps(rows)
    assert "1234567" not in json.dumps(rows)

    report = json.loads(
        (destination / "audit/sanitization-report.json").read_text()
    )
    assert report["source_alias"] == "example/TEST101"
    assert report["policy"]["absolute_source_paths_recorded"] is False
    assert "Test Student" not in json.dumps(report)


def test__sanitize__defaults_to_excluding_unknown_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "unknown.txt").write_text("unknown", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    (source / "linked.txt").symlink_to(outside)

    CourseSanitizer().sanitize(source, tmp_path / "legacy", policy())

    rows = list(
        csv.DictReader(
            (tmp_path / "legacy/audit/classification.csv").open(
                encoding="utf-8"
            )
        )
    )
    assert len(rows) == 2
    assert all(row["action"] == "excluded" for row in rows)
    unknown = next(row for row in rows if row["source_path"] == "unknown.txt")
    linked = next(row for row in rows if row["source_path"] == "linked.txt")
    assert unknown["reason"].startswith("not allowlisted")
    assert linked["reason"].startswith("symbolic link excluded")


def test__sanitize__copies_exclusions_privately_and_writes_biblatex(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "Books").mkdir(parents=True)
    original = source / "Books/reference.pdf"
    original.write_bytes(b"private reference")
    private_policy = replace(
        policy(),
        copy_excluded=True,
        provenance=(
            ProvenanceEntry(
                key="authorTestReference",
                entry_type="unpublished",
                title="Reference",
                author="Author, Example",
                year="2013",
                source_path="Books/reference.pdf",
                note="Private source copy only.",
            ),
        ),
    )
    destination = tmp_path / "legacy"

    CourseSanitizer().sanitize(source, destination, private_policy)

    private_copy = destination / "private-sources/Books/reference.pdf"
    assert private_copy.read_bytes() == b"private reference"
    assert "private-sources/Books/reference.pdf" not in (
        destination / "MANIFEST.sha256"
    ).read_text()
    assert "private-sources/Books/reference.pdf" in (
        destination / "audit/private-sources.sha256"
    ).read_text()
    bibliography = (destination / "provenance.bib").read_text()
    assert "@unpublished{authorTestReference" in bibliography
    assert "author = {Author, Example}" in bibliography
    assert "file = {private-sources/Books/reference.pdf}" in bibliography
    assert "sourcesha256 = {" in bibliography
    assert "artifactsha256" not in bibliography


def test__sanitize__preserves_date_like_filename_numbers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "Homework/week-01").mkdir(parents=True)
    (source / "Homework/week-01/example20130211.txt").write_text(
        "historical example",
        encoding="utf-8",
    )

    CourseSanitizer().sanitize(source, tmp_path / "legacy", policy())

    retained = tmp_path / (
        "legacy/student-work/Homework/week-01/example20130211.txt"
    )
    assert retained.read_text(encoding="utf-8") == "historical example"


def test__sanitize__preserves_existing_destination_when_scan_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "Homework/week-01").mkdir(parents=True)
    (source / "Homework/week-01/answer.txt").write_text(
        "api_key=do-not-copy",
        encoding="utf-8",
    )
    destination = tmp_path / "legacy"
    destination.mkdir()
    marker = destination / "existing.txt"
    marker.write_text("preserved", encoding="utf-8")

    with pytest.raises(SanitizationError, match="possible secret"):
        CourseSanitizer().sanitize(
            source,
            destination,
            policy(),
            replace=True,
        )

    assert marker.read_text(encoding="utf-8") == "preserved"


def test__policy_from_json__rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text('{"schema_version": 2}', encoding="utf-8")

    with pytest.raises(SanitizationError, match="schema_version 1"):
        SanitizationPolicy.from_json(path)


def test__policy__rejects_absolute_source_alias() -> None:
    with pytest.raises(SanitizationError, match="source_alias"):
        replace(policy(), source_alias="/Users/example/private-course")


def test__sanitize__rejects_ambiguous_redacted_source_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "alice@example.com.txt").write_text("first", encoding="utf-8")
    (source / "bob@example.com.txt").write_text("second", encoding="utf-8")

    with pytest.raises(SanitizationError, match="ambiguous after redaction"):
        CourseSanitizer().sanitize(source, tmp_path / "legacy", policy())

    assert not (tmp_path / "legacy").exists()
