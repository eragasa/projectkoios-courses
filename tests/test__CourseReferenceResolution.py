from __future__ import annotations

import json
from pathlib import Path

import pytest
from projectkoios.courses import (
    CourseAuthoringManifest,
    CourseReferenceDisplayCitekeyStatus,
    CourseReferenceLocatorBinding,
    CourseReferenceResolutionError,
    CourseReferenceResolutionProjector,
    CourseSourceInventory,
    CourseSourceInventoryProjector,
)


def _write_manifest(tmp_path: Path) -> CourseAuthoringManifest:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_id": "projectkoios.courses.authoring-manifest",
                "schema_version": 1,
                "course_id": "example.physics",
                "title": "Example Physics",
                "root_alias": "private-course",
                "main_tex": "main.tex",
                "notebook_directories": ["notebooks"],
                "reference_scope": {
                    "preserved_citekeys": ["Observed2026"],
                    "external_bindings": [
                        {
                            "owner": "projectkoios-references",
                            "source_id": "catalog-collection:example",
                            "source_revision": f"sha256:{'a' * 64}",
                            "identity_status": "unaccepted-candidate",
                            "citekey_status": "proposed-noncanonical",
                            "citekeys": ["proposed2026", "other2026"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return CourseAuthoringManifest.from_json(path)


def _inventory(
    tmp_path: Path,
) -> tuple[CourseAuthoringManifest, CourseSourceInventory]:
    manifest = _write_manifest(tmp_path)
    root = tmp_path / "course"
    root.mkdir()
    (root / "notebooks").mkdir()
    (root / "main.tex").write_text(
        """\\documentclass{book}
\\begin{document}
\\cite{Observed2026,proposed2026,other2026}
\\bibliography{references}
\\end{document}
""",
        encoding="utf-8",
    )
    (root / "references.bib").write_text(
        "@book{Observed2026, title={Observed}}\n"
        "@book{proposed2026, title={Proposed}}\n"
        "@book{other2026, title={Other}}\n",
        encoding="utf-8",
    )
    inventory = CourseSourceInventoryProjector().project(
        manifest=manifest,
        course_root=root,
    )
    return manifest, inventory


def _bindings() -> tuple[CourseReferenceLocatorBinding, ...]:
    return (
        CourseReferenceLocatorBinding(
            ordinal=1,
            display_citekey="Observed2026",
            display_citekey_status=(
                CourseReferenceDisplayCitekeyStatus.OBSERVED
            ),
            reference_locator="reference-observation:sha256:1111",
        ),
        CourseReferenceLocatorBinding(
            ordinal=2,
            display_citekey="proposed2026",
            display_citekey_status=(
                CourseReferenceDisplayCitekeyStatus.PROPOSED_NONCANONICAL
            ),
            reference_locator="reference-observation:sha256:2222",
        ),
        CourseReferenceLocatorBinding(
            ordinal=3,
            display_citekey="other2026",
            display_citekey_status=(
                CourseReferenceDisplayCitekeyStatus.PROPOSED_NONCANONICAL
            ),
            reference_locator="reference-observation:sha256:3333",
        ),
    )


def test__project__creates_versioned_revision_bound_ordered_scope(
    tmp_path: Path,
) -> None:
    manifest, inventory = _inventory(tmp_path)
    projector = CourseReferenceResolutionProjector()

    first = projector.project(
        manifest=manifest,
        inventory=inventory,
        locator_bindings=_bindings(),
    )
    second = projector.project(
        manifest=manifest,
        inventory=inventory,
        locator_bindings=_bindings(),
    )
    payload = first.as_dict()

    assert first == second
    assert first.scope_id == second.scope_id
    assert first.total == 3
    assert payload["contract_name"] == "course-reference-resolution-scope"
    assert payload["contract_version"] == "1.0"
    assert payload["manifest_id"] == manifest.manifest_id
    assert payload["manifest_sha256"] == manifest.manifest_sha256
    assert payload["inventory_id"] == inventory.inventory_id
    assert [value.origin.value for value in first.selections] == [
        "preserved-manuscript",
        "directed-reference-scope",
        "directed-reference-scope",
    ]
    assert [
        value.display_citekey_status.value for value in first.selections
    ] == [
        "observed",
        "proposed-noncanonical",
        "proposed-noncanonical",
    ]
    assert payload["selections"][0]["previous_selection_id"] is None
    assert payload["selections"][0]["next_selection_id"] == (
        first.selections[1].selection_id
    )
    assert payload["selections"][2]["next_selection_id"] is None


def test__navigation__derives_previous_next_and_windows_purely(
    tmp_path: Path,
) -> None:
    manifest, inventory = _inventory(tmp_path)
    scope = CourseReferenceResolutionProjector().project(
        manifest=manifest,
        inventory=inventory,
        locator_bindings=_bindings(),
    )

    middle = scope.selections[1].selection_id
    assert (
        scope.previous_selection_id(middle) == scope.selections[0].selection_id
    )
    assert scope.next_selection_id(middle) == scope.selections[2].selection_id
    assert scope.previous_selection_id(scope.selections[0].selection_id) is None
    assert scope.next_selection_id(scope.selections[2].selection_id) is None

    first = scope.window(offset=0, limit=2)
    second = scope.window(offset=2, limit=2)
    assert first.previous_offset is None
    assert first.next_offset == 2
    assert [value.ordinal for value in first.selections] == [1, 2]
    assert second.previous_offset == 0
    assert second.next_offset is None
    assert [value.ordinal for value in second.selections] == [3]
    assert first == scope.window(offset=0, limit=2)


def test__project__rejects_missing_extra_and_duplicate_locators(
    tmp_path: Path,
) -> None:
    manifest, inventory = _inventory(tmp_path)
    projector = CourseReferenceResolutionProjector()
    bindings = _bindings()

    with pytest.raises(
        CourseReferenceResolutionError,
        match="missing selected references",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=bindings[:-1],
        )
    with pytest.raises(
        CourseReferenceResolutionError,
        match="extra selected references",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=bindings + (bindings[-1],),
        )
    duplicate = CourseReferenceLocatorBinding(
        ordinal=3,
        display_citekey="other2026",
        display_citekey_status=(
            CourseReferenceDisplayCitekeyStatus.PROPOSED_NONCANONICAL
        ),
        reference_locator=bindings[1].reference_locator,
    )
    with pytest.raises(
        CourseReferenceResolutionError,
        match="must be one-to-one",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=bindings[:2] + (duplicate,),
        )


def test__project__rejects_order_or_status_instead_of_joining_by_citekey(
    tmp_path: Path,
) -> None:
    manifest, inventory = _inventory(tmp_path)
    projector = CourseReferenceResolutionProjector()
    bindings = _bindings()
    swapped = (
        bindings[0],
        CourseReferenceLocatorBinding(
            ordinal=2,
            display_citekey=bindings[2].display_citekey,
            display_citekey_status=bindings[2].display_citekey_status,
            reference_locator=bindings[2].reference_locator,
        ),
        CourseReferenceLocatorBinding(
            ordinal=3,
            display_citekey=bindings[1].display_citekey,
            display_citekey_status=bindings[1].display_citekey_status,
            reference_locator=bindings[1].reference_locator,
        ),
    )

    with pytest.raises(
        CourseReferenceResolutionError,
        match="citekeys differ from course selection order",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=swapped,
        )
    wrong_status = CourseReferenceLocatorBinding(
        ordinal=1,
        display_citekey="Observed2026",
        display_citekey_status=(
            CourseReferenceDisplayCitekeyStatus.PROPOSED_NONCANONICAL
        ),
        reference_locator=bindings[0].reference_locator,
    )
    with pytest.raises(
        CourseReferenceResolutionError,
        match="status differs from course selection origin",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=(wrong_status,) + bindings[1:],
        )


def test__project__rejects_stale_manifest_and_inventory(tmp_path: Path) -> None:
    manifest, inventory = _inventory(tmp_path)
    projector = CourseReferenceResolutionProjector()
    object.__setattr__(manifest, "manifest_id", f"manifest:sha256:{'f' * 64}")

    with pytest.raises(
        CourseReferenceResolutionError,
        match="manifest identity is stale",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=_bindings(),
        )

    manifest, inventory = _inventory(tmp_path / "second")
    object.__setattr__(
        inventory, "inventory_id", f"inventory:sha256:{'f' * 64}"
    )
    with pytest.raises(
        CourseReferenceResolutionError,
        match="inventory identity is stale",
    ):
        projector.project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=_bindings(),
        )


def test__project__rejects_stale_locator_binding(tmp_path: Path) -> None:
    manifest, inventory = _inventory(tmp_path)
    bindings = _bindings()
    object.__setattr__(bindings[0], "binding_id", f"binding:sha256:{'f' * 64}")

    with pytest.raises(
        CourseReferenceResolutionError,
        match="locator binding identity is stale",
    ):
        CourseReferenceResolutionProjector().project(
            manifest=manifest,
            inventory=inventory,
            locator_bindings=bindings,
        )


def test__project__rejects_incomplete_inventory(tmp_path: Path) -> None:
    manifest, inventory = _inventory(tmp_path)
    stale_root = tmp_path / "incomplete-course"
    stale_root.mkdir()
    (stale_root / "notebooks").mkdir()
    (stale_root / "main.tex").write_text(
        "\\documentclass{book}\n\\begin{document}None\\end{document}\n",
        encoding="utf-8",
    )
    incomplete = CourseSourceInventoryProjector().project(
        manifest=manifest,
        course_root=stale_root,
    )
    assert incomplete.reference_scope_status == "incomplete"

    with pytest.raises(
        CourseReferenceResolutionError,
        match="inventory must be complete",
    ):
        CourseReferenceResolutionProjector().project(
            manifest=manifest,
            inventory=incomplete,
            locator_bindings=_bindings(),
        )


def test__limits_and_unknown_navigation_fail_closed(tmp_path: Path) -> None:
    manifest, inventory = _inventory(tmp_path)
    scope = CourseReferenceResolutionProjector().project(
        manifest=manifest,
        inventory=inventory,
        locator_bindings=_bindings(),
    )

    with pytest.raises(CourseReferenceResolutionError, match="limit exceeds"):
        scope.window(offset=0, limit=101)
    with pytest.raises(CourseReferenceResolutionError, match="offset exceeds"):
        scope.window(offset=4, limit=1)
    with pytest.raises(CourseReferenceResolutionError, match="absent"):
        scope.next_selection_id("course-reference-selection:sha256:missing")
    with pytest.raises(
        CourseReferenceResolutionError,
        match="bounded opaque identity",
    ):
        CourseReferenceLocatorBinding(
            ordinal=1,
            display_citekey="Observed2026",
            display_citekey_status=(
                CourseReferenceDisplayCitekeyStatus.OBSERVED
            ),
            reference_locator="x" * 1_025,
        )
