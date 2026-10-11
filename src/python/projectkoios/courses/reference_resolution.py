"""Course-owned scope and navigation for reference-resolution composition."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from .authoring_inventory import (
    CourseAuthoringInventoryError,
    CourseAuthoringManifest,
    CourseSourceInventory,
)
from .reference_resolution_taxonomy import (
    CourseReferenceActionizer,
    CourseReferenceActionRequest,
    CourseReferenceActionResult,
    CourseReferenceDataObject,
)

_CITEKEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,199}")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,1023}")


class CourseReferenceResolutionError(ValueError):
    """Raised when course reference-resolution scope is incomplete or stale."""


class CourseReferenceSelectionOrigin(StrEnum):
    """Identify the course-owned reason that selected one reference."""

    PRESERVED_MANUSCRIPT = "preserved-manuscript"
    DIRECTED_REFERENCE_SCOPE = "directed-reference-scope"


class CourseReferenceRole(StrEnum):
    """Identify the bounded role of a selected reference in the course."""

    MANUSCRIPT_BIBLIOGRAPHY = "manuscript-bibliography"


class CourseReferenceDisplayCitekeyStatus(StrEnum):
    """Describe a display locator without granting citekey authority."""

    OBSERVED = "observed"
    PROPOSED_NONCANONICAL = "proposed-noncanonical"


@dataclass(frozen=True, slots=True, kw_only=True)
class CourseReferenceLocatorBinding(CourseReferenceDataObject):
    """Carry one opaque References-owned locator into Courses composition."""

    CONTRACT_VERSION: ClassVar[str] = "1.0"
    MAX_LOCATOR_CHARACTERS: ClassVar[int] = 1_024
    REFERENCE_OWNER: ClassVar[str] = "projectkoios-references"

    ordinal: int
    display_citekey: str
    display_citekey_status: CourseReferenceDisplayCitekeyStatus
    reference_locator: str
    binding_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_positive_int(self.ordinal, label="locator ordinal")
        _require_citekey(self.display_citekey, label="display citekey")
        if not isinstance(
            self.display_citekey_status,
            CourseReferenceDisplayCitekeyStatus,
        ):
            raise CourseReferenceResolutionError(
                "display citekey status must be explicit"
            )
        _require_identity(
            self.reference_locator,
            label="References locator",
            maximum=self.MAX_LOCATOR_CHARACTERS,
        )
        object.__setattr__(
            self,
            "binding_id",
            _content_identity(
                "course-reference-locator-binding",
                {
                    "contract_version": self.CONTRACT_VERSION,
                    "display_citekey": self.display_citekey,
                    "display_citekey_status": (
                        self.display_citekey_status.value
                    ),
                    "ordinal": self.ordinal,
                    "reference_locator": self.reference_locator,
                    "reference_owner": self.REFERENCE_OWNER,
                },
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CourseReferenceSelection(CourseReferenceDataObject):
    """Represent one immutable course-selected reference queue item."""

    CONTRACT_VERSION: ClassVar[str] = "1.0"
    REFERENCE_OWNER: ClassVar[str] = "projectkoios-references"

    course_id: str
    manifest_id: str
    inventory_id: str
    ordinal: int
    origin: CourseReferenceSelectionOrigin
    role: CourseReferenceRole
    display_citekey: str
    display_citekey_status: CourseReferenceDisplayCitekeyStatus
    reference_locator: str
    locator_binding_id: str
    selection_id: str = field(init=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("course ID", self.course_id),
            ("manifest ID", self.manifest_id),
            ("inventory ID", self.inventory_id),
            ("References locator", self.reference_locator),
            ("locator binding ID", self.locator_binding_id),
        ):
            _require_identity(value, label=label)
        _require_positive_int(self.ordinal, label="selection ordinal")
        _require_citekey(self.display_citekey, label="display citekey")
        if not isinstance(self.origin, CourseReferenceSelectionOrigin):
            raise CourseReferenceResolutionError(
                "selection origin must be explicit"
            )
        if self.role is not CourseReferenceRole.MANUSCRIPT_BIBLIOGRAPHY:
            raise CourseReferenceResolutionError(
                "manuscript-bibliography role is required"
            )
        if not isinstance(
            self.display_citekey_status,
            CourseReferenceDisplayCitekeyStatus,
        ):
            raise CourseReferenceResolutionError(
                "display citekey status must be explicit"
            )
        object.__setattr__(
            self,
            "selection_id",
            _content_identity(
                "course-reference-selection",
                {
                    "contract_version": self.CONTRACT_VERSION,
                    "course_id": self.course_id,
                    "display_citekey": self.display_citekey,
                    "display_citekey_status": (
                        self.display_citekey_status.value
                    ),
                    "inventory_id": self.inventory_id,
                    "locator_binding_id": self.locator_binding_id,
                    "manifest_id": self.manifest_id,
                    "ordinal": self.ordinal,
                    "origin": self.origin.value,
                    "reference_locator": self.reference_locator,
                    "reference_owner": self.REFERENCE_OWNER,
                    "role": self.role.value,
                },
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CourseReferenceResolutionRequest(CourseReferenceActionRequest):
    """Carry complete inputs for one course resolution-scope action."""

    manifest: CourseAuthoringManifest
    inventory: CourseSourceInventory
    locator_bindings: tuple[CourseReferenceLocatorBinding, ...]

    def __post_init__(self) -> None:
        if type(self.locator_bindings) is not tuple or any(
            type(value) is not CourseReferenceLocatorBinding
            for value in self.locator_bindings
        ):
            raise CourseReferenceResolutionError(
                "locator bindings must be an exact immutable tuple"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class CourseReferenceResolutionWindow(CourseReferenceActionResult):
    """Represent one deterministic bounded window over a resolution scope."""

    CONTRACT_VERSION: ClassVar[str] = "1.0"
    MAX_WINDOW_SIZE: ClassVar[int] = 100

    scope_id: str
    offset: int
    limit: int
    total: int
    selections: tuple[CourseReferenceSelection, ...]
    previous_offset: int | None
    next_offset: int | None
    window_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identity(self.scope_id, label="resolution scope ID")
        _require_nonnegative_int(self.offset, label="window offset")
        _require_positive_int(self.limit, label="window limit")
        if self.limit > self.MAX_WINDOW_SIZE:
            raise CourseReferenceResolutionError(
                "window limit exceeds its bound"
            )
        _require_nonnegative_int(self.total, label="window total")
        if self.offset > self.total:
            raise CourseReferenceResolutionError("window offset exceeds total")
        if type(self.selections) is not tuple or any(
            type(value) is not CourseReferenceSelection
            for value in self.selections
        ):
            raise CourseReferenceResolutionError(
                "window selections must be an exact immutable tuple"
            )
        if len(self.selections) > self.limit:
            raise CourseReferenceResolutionError(
                "window selections exceed the requested limit"
            )
        expected_ordinals = tuple(
            range(self.offset + 1, self.offset + len(self.selections) + 1)
        )
        if (
            tuple(value.ordinal for value in self.selections)
            != expected_ordinals
        ):
            raise CourseReferenceResolutionError(
                "window selections differ from contiguous scope order"
            )
        expected_previous = (
            max(0, self.offset - self.limit) if self.offset else None
        )
        consumed = self.offset + len(self.selections)
        expected_next = consumed if consumed < self.total else None
        if self.previous_offset != expected_previous:
            raise CourseReferenceResolutionError(
                "previous window offset is inconsistent"
            )
        if self.next_offset != expected_next:
            raise CourseReferenceResolutionError(
                "next window offset is inconsistent"
            )
        object.__setattr__(
            self,
            "window_id",
            _content_identity(
                "course-reference-resolution-window",
                {
                    "contract_version": self.CONTRACT_VERSION,
                    "limit": self.limit,
                    "next_offset": self.next_offset,
                    "offset": self.offset,
                    "previous_offset": self.previous_offset,
                    "scope_id": self.scope_id,
                    "selection_ids": tuple(
                        value.selection_id for value in self.selections
                    ),
                    "total": self.total,
                },
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CourseReferenceResolutionScope(CourseReferenceActionResult):
    """Own one exact ordered course reference-resolution queue."""

    CONTRACT_NAME: ClassVar[str] = "course-reference-resolution-scope"
    CONTRACT_VERSION: ClassVar[str] = "1.0"
    MAX_SELECTIONS: ClassVar[int] = 10_000

    course_id: str
    course_title: str
    manifest_id: str
    manifest_sha256: str
    inventory_id: str
    selections: tuple[CourseReferenceSelection, ...]
    total: int = field(init=False)
    scope_id: str = field(init=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("course ID", self.course_id),
            ("manifest ID", self.manifest_id),
            ("inventory ID", self.inventory_id),
        ):
            _require_identity(value, label=label)
        _require_text(self.course_title, label="course title", maximum=500)
        if re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256) is None:
            raise CourseReferenceResolutionError(
                "manifest revision must be lowercase SHA-256"
            )
        if type(self.selections) is not tuple or any(
            type(value) is not CourseReferenceSelection
            for value in self.selections
        ):
            raise CourseReferenceResolutionError(
                "scope selections must be an exact immutable tuple"
            )
        if not self.selections:
            raise CourseReferenceResolutionError(
                "resolution scope requires at least one selection"
            )
        if len(self.selections) > self.MAX_SELECTIONS:
            raise CourseReferenceResolutionError(
                "resolution scope exceeds its selection limit"
            )
        expected_ordinals = tuple(range(1, len(self.selections) + 1))
        if (
            tuple(value.ordinal for value in self.selections)
            != expected_ordinals
        ):
            raise CourseReferenceResolutionError(
                "scope selections must be contiguous and ordered"
            )
        if any(
            value.course_id != self.course_id
            or value.manifest_id != self.manifest_id
            or value.inventory_id != self.inventory_id
            for value in self.selections
        ):
            raise CourseReferenceResolutionError(
                "scope selections bind different course revisions"
            )
        selection_ids = tuple(value.selection_id for value in self.selections)
        locators = tuple(value.reference_locator for value in self.selections)
        citekeys = tuple(value.display_citekey for value in self.selections)
        if len(selection_ids) != len(set(selection_ids)):
            raise CourseReferenceResolutionError(
                "scope selection identities must be unique"
            )
        if len(locators) != len(set(locators)):
            raise CourseReferenceResolutionError(
                "References locators must be one-to-one"
            )
        if len(citekeys) != len(set(citekeys)):
            raise CourseReferenceResolutionError(
                "scope display citekeys must be unique"
            )
        object.__setattr__(self, "total", len(self.selections))
        object.__setattr__(
            self,
            "scope_id",
            _content_identity(
                self.CONTRACT_NAME,
                {
                    "contract_version": self.CONTRACT_VERSION,
                    "course_id": self.course_id,
                    "course_title": self.course_title,
                    "inventory_id": self.inventory_id,
                    "manifest_id": self.manifest_id,
                    "manifest_sha256": self.manifest_sha256,
                    "selection_ids": selection_ids,
                },
            ),
        )


class CourseReferenceResolutionActionizer(
    CourseReferenceActionizer[
        CourseReferenceResolutionRequest,
        CourseReferenceResolutionScope,
    ]
):
    """Project explicit course and locator inputs into one immutable scope."""

    __slots__ = ()

    def action(
        self,
        *,
        request: CourseReferenceResolutionRequest,
    ) -> CourseReferenceResolutionScope:
        """Compose one exact course reference-resolution scope."""
        manifest = request.manifest
        inventory = request.inventory
        locator_bindings = request.locator_bindings
        _require_fresh_manifest(manifest)
        _require_fresh_inventory(inventory)
        if inventory.manifest != manifest:
            raise CourseReferenceResolutionError(
                "inventory binds a different course manifest"
            )
        if inventory.reference_scope_status != "complete":
            raise CourseReferenceResolutionError(
                "course reference scope inventory must be complete"
            )
        planned = manifest.planned_citekeys
        if not planned:
            raise CourseReferenceResolutionError(
                "course manifest has no selected references"
            )
        if len(planned) > CourseReferenceResolutionScope.MAX_SELECTIONS:
            raise CourseReferenceResolutionError(
                "course reference scope exceeds its selection limit"
            )
        if len(locator_bindings) < len(planned):
            raise CourseReferenceResolutionError(
                "locator bindings are missing selected references"
            )
        if len(locator_bindings) > len(planned):
            raise CourseReferenceResolutionError(
                "locator bindings contain extra selected references"
            )
        for binding in locator_bindings:
            _require_fresh_locator_binding(binding)
        expected_ordinals = tuple(range(1, len(planned) + 1))
        if (
            tuple(value.ordinal for value in locator_bindings)
            != expected_ordinals
        ):
            raise CourseReferenceResolutionError(
                "locator binding order differs from course selection order"
            )
        if (
            tuple(value.display_citekey for value in locator_bindings)
            != planned
        ):
            raise CourseReferenceResolutionError(
                "locator binding citekeys differ from course selection order"
            )
        locator_values = tuple(
            value.reference_locator for value in locator_bindings
        )
        if len(locator_values) != len(set(locator_values)):
            raise CourseReferenceResolutionError(
                "References locator bindings must be one-to-one"
            )
        selections: list[CourseReferenceSelection] = []
        preserved = set(manifest.preserved_citekeys)
        for binding in locator_bindings:
            if binding.display_citekey in preserved:
                origin = CourseReferenceSelectionOrigin.PRESERVED_MANUSCRIPT
                status = CourseReferenceDisplayCitekeyStatus.OBSERVED
            else:
                origin = CourseReferenceSelectionOrigin.DIRECTED_REFERENCE_SCOPE
                status = (
                    CourseReferenceDisplayCitekeyStatus.PROPOSED_NONCANONICAL
                )
            if binding.display_citekey_status is not status:
                raise CourseReferenceResolutionError(
                    "locator display status differs from course "
                    "selection origin"
                )
            selections.append(
                CourseReferenceSelection(
                    course_id=manifest.course_id,
                    manifest_id=manifest.manifest_id,
                    inventory_id=inventory.inventory_id,
                    ordinal=binding.ordinal,
                    origin=origin,
                    role=CourseReferenceRole.MANUSCRIPT_BIBLIOGRAPHY,
                    display_citekey=binding.display_citekey,
                    display_citekey_status=status,
                    reference_locator=binding.reference_locator,
                    locator_binding_id=binding.binding_id,
                )
            )
        return CourseReferenceResolutionScope(
            course_id=manifest.course_id,
            course_title=manifest.title,
            manifest_id=manifest.manifest_id,
            manifest_sha256=manifest.manifest_sha256,
            inventory_id=inventory.inventory_id,
            selections=tuple(selections),
        )


def course_reference_resolution_previous_selection_id(
    scope: CourseReferenceResolutionScope,
    selection_id: str,
) -> str | None:
    """Return the preceding identity in Courses-owned order."""
    index = _selection_index(scope, selection_id)
    return None if index == 0 else scope.selections[index - 1].selection_id


def course_reference_resolution_next_selection_id(
    scope: CourseReferenceResolutionScope,
    selection_id: str,
) -> str | None:
    """Return the following identity in Courses-owned order."""
    index = _selection_index(scope, selection_id)
    if index + 1 == scope.total:
        return None
    return scope.selections[index + 1].selection_id


def course_reference_resolution_window(
    scope: CourseReferenceResolutionScope,
    *,
    offset: int,
    limit: int,
) -> CourseReferenceResolutionWindow:
    """Return one deterministic bounded selection window."""
    _require_nonnegative_int(offset, label="window offset")
    _require_positive_int(limit, label="window limit")
    if offset > scope.total:
        raise CourseReferenceResolutionError("window offset exceeds total")
    if limit > CourseReferenceResolutionWindow.MAX_WINDOW_SIZE:
        raise CourseReferenceResolutionError("window limit exceeds its bound")
    selections = scope.selections[offset : offset + limit]
    previous_offset = max(0, offset - limit) if offset else None
    consumed = offset + len(selections)
    next_offset = consumed if consumed < scope.total else None
    return CourseReferenceResolutionWindow(
        scope_id=scope.scope_id,
        offset=offset,
        limit=limit,
        total=scope.total,
        selections=selections,
        previous_offset=previous_offset,
        next_offset=next_offset,
    )


def course_reference_resolution_scope_as_dict(
    scope: CourseReferenceResolutionScope,
) -> dict[str, object]:
    """Return the stable course scope with per-item navigation."""
    values: list[dict[str, object]] = []
    for index, selection in enumerate(scope.selections):
        value = _selection_as_dict(selection)
        value["previous_selection_id"] = (
            None if index == 0 else scope.selections[index - 1].selection_id
        )
        value["next_selection_id"] = (
            None
            if index + 1 == scope.total
            else scope.selections[index + 1].selection_id
        )
        values.append(value)
    return {
        "contract_name": scope.CONTRACT_NAME,
        "contract_version": scope.CONTRACT_VERSION,
        "course_id": scope.course_id,
        "course_title": scope.course_title,
        "inventory_id": scope.inventory_id,
        "manifest_id": scope.manifest_id,
        "manifest_sha256": scope.manifest_sha256,
        "scope_id": scope.scope_id,
        "selections": values,
        "total": scope.total,
    }


def _selection_index(
    scope: CourseReferenceResolutionScope,
    selection_id: str,
) -> int:
    _require_identity(selection_id, label="selection ID")
    for index, selection in enumerate(scope.selections):
        if selection.selection_id == selection_id:
            return index
    raise CourseReferenceResolutionError(
        "selection identity is absent from this scope"
    )


def _selection_as_dict(
    selection: CourseReferenceSelection,
) -> dict[str, object]:
    return {
        "course_id": selection.course_id,
        "display_citekey": selection.display_citekey,
        "display_citekey_status": selection.display_citekey_status.value,
        "inventory_id": selection.inventory_id,
        "locator_binding_id": selection.locator_binding_id,
        "manifest_id": selection.manifest_id,
        "ordinal": selection.ordinal,
        "origin": selection.origin.value,
        "reference_locator": selection.reference_locator,
        "reference_owner": selection.REFERENCE_OWNER,
        "role": selection.role.value,
        "selection_id": selection.selection_id,
    }


def _require_fresh_locator_binding(
    binding: CourseReferenceLocatorBinding,
) -> None:
    fresh = CourseReferenceLocatorBinding(
        ordinal=binding.ordinal,
        display_citekey=binding.display_citekey,
        display_citekey_status=binding.display_citekey_status,
        reference_locator=binding.reference_locator,
    )
    if fresh != binding:
        raise CourseReferenceResolutionError(
            "course reference locator binding identity is stale"
        )


def _require_fresh_manifest(manifest: CourseAuthoringManifest) -> None:
    if type(manifest) is not CourseAuthoringManifest:
        raise CourseReferenceResolutionError(
            "manifest must be CourseAuthoringManifest"
        )
    try:
        fresh = CourseAuthoringManifest(
            schema_id=manifest.schema_id,
            schema_version=manifest.schema_version,
            course_id=manifest.course_id,
            title=manifest.title,
            root_alias=manifest.root_alias,
            main_tex=manifest.main_tex,
            notebook_directories=manifest.notebook_directories,
            preserved_citekeys=manifest.preserved_citekeys,
            external_reference_bindings=(manifest.external_reference_bindings),
            manifest_sha256=manifest.manifest_sha256,
        )
    except CourseAuthoringInventoryError as error:
        raise CourseReferenceResolutionError(
            "course manifest is stale"
        ) from error
    if fresh != manifest:
        raise CourseReferenceResolutionError(
            "course manifest identity is stale"
        )


def _require_fresh_inventory(inventory: CourseSourceInventory) -> None:
    if type(inventory) is not CourseSourceInventory:
        raise CourseReferenceResolutionError(
            "inventory must be CourseSourceInventory"
        )
    _require_fresh_manifest(inventory.manifest)
    try:
        fresh = CourseSourceInventory(
            manifest=inventory.manifest,
            sources=inventory.sources,
            includes=inventory.includes,
            structure=inventory.structure,
            citations=inventory.citations,
            inline_bibliography=inventory.inline_bibliography,
            bibliography_files=inventory.bibliography_files,
            notebooks=inventory.notebooks,
            missing_preserved_citekeys=(inventory.missing_preserved_citekeys),
            unplanned_inline_citekeys=inventory.unplanned_inline_citekeys,
            unresolved_citation_citekeys=(
                inventory.unresolved_citation_citekeys
            ),
            missing_planned_citekeys=inventory.missing_planned_citekeys,
            unplanned_bibliography_citekeys=(
                inventory.unplanned_bibliography_citekeys
            ),
            duplicate_bibliography_citekeys=(
                inventory.duplicate_bibliography_citekeys
            ),
        )
    except CourseAuthoringInventoryError as error:
        raise CourseReferenceResolutionError(
            "course source inventory is stale"
        ) from error
    if fresh != inventory:
        raise CourseReferenceResolutionError(
            "course source inventory identity is stale"
        )


def _content_identity(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _require_citekey(value: str, *, label: str) -> None:
    if type(value) is not str or _CITEKEY.fullmatch(value) is None:
        raise CourseReferenceResolutionError(f"{label} is invalid")


def _require_identity(
    value: str,
    *,
    label: str,
    maximum: int = 1_024,
) -> None:
    if (
        type(value) is not str
        or len(value) > maximum
        or _IDENTITY.fullmatch(value) is None
    ):
        raise CourseReferenceResolutionError(
            f"{label} must be a bounded opaque identity"
        )


def _require_text(value: str, *, label: str, maximum: int) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise CourseReferenceResolutionError(
            f"{label} must contain 1 to {maximum} trimmed characters"
        )


def _require_positive_int(value: int, *, label: str) -> None:
    if type(value) is not int or value < 1:
        raise CourseReferenceResolutionError(
            f"{label} must be a positive built-in integer"
        )


def _require_nonnegative_int(value: int, *, label: str) -> None:
    if type(value) is not int or value < 0:
        raise CourseReferenceResolutionError(
            f"{label} must be a nonnegative built-in integer"
        )
