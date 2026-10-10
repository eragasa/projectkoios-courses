from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from projectkoios.courses import (
    CourseAuthoringInventoryError,
    CourseAuthoringManifest,
    CourseSourceInventoryProjector,
)


def _manifest_value() -> dict[str, object]:
    return {
        "schema_id": "projectkoios.courses.authoring-manifest",
        "schema_version": 1,
        "course_id": "example.physics",
        "title": "Example Physics",
        "root_alias": "private-course",
        "main_tex": "main.tex",
        "notebook_directories": ["notebooks"],
        "reference_scope": {
            "preserved_citekeys": ["Existing2026"],
            "external_bindings": [
                {
                    "owner": "projectkoios-references",
                    "source_id": "catalog-collection:example",
                    "source_revision": f"sha256:{'a' * 64}",
                    "identity_status": "unaccepted-candidate",
                    "citekey_status": "proposed-noncanonical",
                    "citekeys": ["candidate2026"],
                }
            ],
        },
    }


def _write_manifest(tmp_path: Path, value: object | None = None) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(_manifest_value() if value is None else value),
        encoding="utf-8",
    )
    return path


def _write_course(tmp_path: Path) -> Path:
    root = tmp_path / "course"
    (root / "chapters").mkdir(parents=True)
    (root / "notebooks").mkdir()
    (root / "main.tex").write_text(
        """\\documentclass{book}
% \\input{missing.tex}
\\begin{document}
\\part{Foundations}
\\input{chapters/first}
\\bibliography{references}
\\begin{thebibliography}{9}
\\bibitem{Existing2026} Existing source.
\\end{thebibliography}
\\end{document}
""",
        encoding="utf-8",
    )
    (root / "chapters" / "first.tex").write_text(
        """\\chapter{A {Nested} Title}
\\section{Question}
A supported claim \\cite[see][p.~2]{Existing2026,candidate2026}.
""",
        encoding="utf-8",
    )
    (root / "references.bib").write_text(
        "@book{Existing2026, title={Existing source}}\n",
        encoding="utf-8",
    )
    (root / "notebooks" / "chapter.ipynb").write_text(
        '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}\n',
        encoding="utf-8",
    )
    return root


def test__project__emits_deterministic_private_path_free_inventory(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest(tmp_path)
    root = _write_course(tmp_path)
    manifest = CourseAuthoringManifest.from_json(manifest_path)
    projector = CourseSourceInventoryProjector()

    first = projector.project(manifest=manifest, course_root=root)
    second = projector.project(manifest=manifest, course_root=root)
    encoded = first.to_json()
    payload = json.loads(encoded)

    assert first == second
    assert first.inventory_id == second.inventory_id
    assert str(root) not in encoded
    assert str(tmp_path) not in encoded
    assert payload["schema_id"] == (
        "projectkoios.courses.course-source-inventory"
    )
    assert payload["course"]["root_alias"] == "private-course"
    assert [item["path"] for item in payload["manuscript"]["sources"]] == [
        "main.tex",
        "chapters/first.tex",
    ]
    assert payload["manuscript"]["includes"] == [
        {
            "command": "input",
            "line": 5,
            "source_path": "main.tex",
            "target_path": "chapters/first.tex",
        }
    ]
    assert [item["title"] for item in payload["manuscript"]["structure"]] == [
        "Foundations",
        "A {Nested} Title",
        "Question",
    ]
    assert payload["manuscript"]["cited_citekeys"] == [
        "Existing2026",
        "candidate2026",
    ]
    assert payload["reference_scope"]["planned_citekey_count"] == 2
    assert payload["reference_scope"]["status"] == "complete"
    assert payload["manuscript"]["bibliography_files"][0]["path"] == (
        "references.bib"
    )
    assert payload["manuscript"]["notebooks"][0]["path"] == (
        "notebooks/chapter.ipynb"
    )


def test__solid_state_manifest__declares_one_explicit_fourteen_work_scope() -> (
    None
):
    path = (
        Path(__file__).resolve().parents[1]
        / "manifests"
        / "dlsu.phy627m.gs1.authoring.json"
    )
    manifest = CourseAuthoringManifest.from_json(path)

    assert manifest.preserved_citekeys == (
        "deBroglie1925",
        "Shankar1994",
        "Hellmann1937",
        "Feynman1939",
        "Xiao2010",
    )
    assert manifest.planned_citekeys == manifest.preserved_citekeys + (
        "kittel2005",
        "simon2013",
        "ashcroftMermin1976",
        "marder2010",
        "szeNg2007",
        "neamen2003",
        "szeLee2012",
        "yuCardona2010",
        "rudan2015",
    )
    assert {
        (binding.identity_status, binding.citekey_status)
        for binding in manifest.external_reference_bindings
    } == {("unaccepted-candidate", "proposed-noncanonical")}
    assert [
        binding.source_revision
        for binding in manifest.external_reference_bindings
    ] == [
        "sha256:7dd122cea8987c9e08f0d54fca1742e2b99ef2d05aa560d34ba67307965d1506",
        "sha256:584bd4512fd3c47fe00251f36da4f36974f1fdd2adef0f1036d75a67a92429e7",
    ]


def test__manifest__retains_exact_bytes_and_normalized_identity(
    tmp_path: Path,
) -> None:
    path = _write_manifest(tmp_path)
    manifest = CourseAuthoringManifest.from_json(path)

    assert (
        manifest.manifest_sha256
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert manifest.manifest_id.startswith("course-authoring-manifest:sha256:")
    assert manifest.planned_citekeys == ("Existing2026", "candidate2026")


def test__project__reports_reference_scope_gaps(tmp_path: Path) -> None:
    manifest = CourseAuthoringManifest.from_json(_write_manifest(tmp_path))
    root = _write_course(tmp_path)
    main = root / "main.tex"
    main.write_text(
        main.read_text(encoding="utf-8").replace(
            "\\bibitem{Existing2026}",
            "\\bibitem{Other2026}",
        ),
        encoding="utf-8",
    )
    chapter = root / "chapters" / "first.tex"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace(
            "candidate2026}",
            "unknown2026}",
        ),
        encoding="utf-8",
    )

    inventory = CourseSourceInventoryProjector().project(
        manifest=manifest,
        course_root=root,
    )

    assert inventory.reference_scope_status == "incomplete"
    assert inventory.missing_preserved_citekeys == ("Existing2026",)
    assert inventory.unplanned_inline_citekeys == ("Other2026",)
    assert inventory.unresolved_citation_citekeys == (
        "Existing2026",
        "unknown2026",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("main_tex", "../main.tex", "POSIX relative path"),
        ("main_tex", "/private/main.tex", "POSIX relative path"),
        ("main_tex", "C:/private/main.tex", "POSIX relative path"),
        ("main_tex", "chapters/./main.tex", "POSIX relative path"),
        ("schema_version", 2, "unsupported manifest schema version"),
    ],
)
def test__manifest__rejects_unsupported_or_unsafe_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    manifest = _manifest_value()
    manifest[field] = value

    with pytest.raises(CourseAuthoringInventoryError, match=message):
        CourseAuthoringManifest.from_json(_write_manifest(tmp_path, manifest))


def test__manifest__rejects_duplicate_json_fields(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema_id":"first","schema_id":"second"}',
        encoding="utf-8",
    )

    with pytest.raises(CourseAuthoringInventoryError, match="repeats field"):
        CourseAuthoringManifest.from_json(path)


def test__manifest__rejects_overlapping_reference_keys(tmp_path: Path) -> None:
    manifest = _manifest_value()
    scope = manifest["reference_scope"]
    assert isinstance(scope, dict)
    bindings = scope["external_bindings"]
    assert isinstance(bindings, list)
    binding = bindings[0]
    assert isinstance(binding, dict)
    binding["citekeys"] = ["Existing2026"]

    with pytest.raises(
        CourseAuthoringInventoryError,
        match="planned bibliography citekeys must be unique",
    ):
        CourseAuthoringManifest.from_json(_write_manifest(tmp_path, manifest))


def test__project__rejects_include_escape(tmp_path: Path) -> None:
    manifest = CourseAuthoringManifest.from_json(_write_manifest(tmp_path))
    root = _write_course(tmp_path)
    (root / "main.tex").write_text(
        "\\input{../outside.tex}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CourseAuthoringInventoryError, match="POSIX relative path"
    ):
        CourseSourceInventoryProjector().project(
            manifest=manifest,
            course_root=root,
        )


def test__project__rejects_symlinked_course_file(tmp_path: Path) -> None:
    manifest = CourseAuthoringManifest.from_json(_write_manifest(tmp_path))
    root = _write_course(tmp_path)
    outside = tmp_path / "outside.tex"
    outside.write_text("\\chapter{Outside}\n", encoding="utf-8")
    included = root / "chapters" / "first.tex"
    included.unlink()
    included.symlink_to(outside)

    with pytest.raises(CourseAuthoringInventoryError, match="symlinks"):
        CourseSourceInventoryProjector().project(
            manifest=manifest,
            course_root=root,
        )
