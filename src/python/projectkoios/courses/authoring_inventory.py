"""Deterministic read-only inventory of one explicit course source root."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

from .identities import validate_nominal_identity

_MANIFEST_SCHEMA_ID = "projectkoios.courses.authoring-manifest"
_INVENTORY_SCHEMA_ID = "projectkoios.courses.course-source-inventory"
_SCHEMA_VERSION = 1
_SHA256_REVISION = re.compile(r"sha256:[0-9a-f]{64}")
_CITEKEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,199}")
_COMMAND = re.compile(
    r"\\(?P<name>"
    r"addbibresource|bibliography|bibitem|include|input|"
    r"subsection|section|chapter|part|cite"
    r")\*?"
)
_HEADING_COMMANDS = frozenset({"part", "chapter", "section", "subsection"})
_CITATION_COMMANDS = frozenset({"cite"})
_MAX_MANIFEST_BYTES = 1_000_000
_MAX_SOURCE_BYTES = 4_000_000
_MAX_TOTAL_SOURCE_BYTES = 32_000_000
_MAX_SOURCE_FILES = 1_000
_MAX_NOTEBOOK_FILES = 1_000
_MAX_TEXT_CHARACTERS = 4_000_000


class CourseAuthoringInventoryError(ValueError):
    """Raised when a manifest or explicit source root cannot be inventoried."""


@dataclass(frozen=True, slots=True)
class ExternalReferenceBinding:
    """Bind course inclusion to an exact References-owned candidate source."""

    owner: str
    source_id: str
    source_revision: str
    identity_status: str
    citekey_status: str
    citekeys: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.owner != "projectkoios-references":
            raise CourseAuthoringInventoryError(
                "external reference owner must be projectkoios-references"
            )
        _validate_trimmed(self.source_id, "reference source ID", maximum=512)
        if _SHA256_REVISION.fullmatch(self.source_revision) is None:
            raise CourseAuthoringInventoryError(
                "reference source revision must use sha256:<digest>"
            )
        if self.identity_status != "unaccepted-candidate":
            raise CourseAuthoringInventoryError(
                "the current slice admits only unaccepted reference candidates"
            )
        if self.citekey_status != "proposed-noncanonical":
            raise CourseAuthoringInventoryError(
                "the current slice admits only proposed noncanonical citekeys"
            )
        _validate_citekeys(self.citekeys, label="external reference citekeys")

    def as_dict(self) -> dict[str, object]:
        """Return the stable external-boundary representation."""

        return {
            "citekey_status": self.citekey_status,
            "citekeys": list(self.citekeys),
            "identity_status": self.identity_status,
            "owner": self.owner,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
        }


@dataclass(frozen=True, slots=True)
class CourseAuthoringManifest:
    """Describe one private course root without storing its absolute path."""

    schema_id: str
    schema_version: int
    course_id: str
    title: str
    root_alias: str
    main_tex: str
    notebook_directories: tuple[str, ...]
    preserved_citekeys: tuple[str, ...]
    external_reference_bindings: tuple[ExternalReferenceBinding, ...]
    manifest_sha256: str = field(repr=False)
    manifest_id: str = field(init=False)

    EXPECTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_id",
            "schema_version",
            "course_id",
            "title",
            "root_alias",
            "main_tex",
            "notebook_directories",
            "reference_scope",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_id != _MANIFEST_SCHEMA_ID:
            raise CourseAuthoringInventoryError(
                "unsupported manifest schema ID"
            )
        if (
            type(self.schema_version) is not int
            or self.schema_version != _SCHEMA_VERSION
        ):
            raise CourseAuthoringInventoryError(
                "unsupported manifest schema version"
            )
        try:
            validate_nominal_identity(self.course_id, label="course identity")
            validate_nominal_identity(
                self.root_alias, label="course root alias"
            )
        except ValueError as error:
            raise CourseAuthoringInventoryError(str(error)) from error
        _validate_trimmed(self.title, "course title", maximum=500)
        _safe_relative_path(self.main_tex, label="main TeX path", suffix=".tex")
        _validate_relative_paths(
            self.notebook_directories,
            label="notebook directories",
        )
        _validate_citekeys(
            self.preserved_citekeys,
            label="preserved citekeys",
        )
        if not self.external_reference_bindings:
            raise CourseAuthoringInventoryError(
                "at least one external reference binding is required"
            )
        if len(
            {binding.source_id for binding in self.external_reference_bindings}
        ) != len(self.external_reference_bindings):
            raise CourseAuthoringInventoryError(
                "external reference source IDs must be unique"
            )
        planned = list(self.preserved_citekeys)
        for binding in self.external_reference_bindings:
            planned.extend(binding.citekeys)
        if len(planned) != len(set(planned)):
            raise CourseAuthoringInventoryError(
                "planned bibliography citekeys must be unique"
            )
        if re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256) is None:
            raise CourseAuthoringInventoryError(
                "manifest digest must be lowercase SHA-256"
            )
        object.__setattr__(
            self,
            "manifest_id",
            _content_identity("course-authoring-manifest", self.as_dict()),
        )

    @property
    def planned_citekeys(self) -> tuple[str, ...]:
        """Return the complete declared bibliography scope in manifest order."""

        return self.preserved_citekeys + tuple(
            citekey
            for binding in self.external_reference_bindings
            for citekey in binding.citekeys
        )

    def as_dict(self) -> dict[str, object]:
        """Return canonical manifest content without file-byte provenance."""

        return {
            "course_id": self.course_id,
            "main_tex": self.main_tex,
            "notebook_directories": list(self.notebook_directories),
            "reference_scope": {
                "external_bindings": [
                    binding.as_dict()
                    for binding in self.external_reference_bindings
                ],
                "preserved_citekeys": list(self.preserved_citekeys),
            },
            "root_alias": self.root_alias,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "title": self.title,
        }

    @classmethod
    def from_json(cls, path: Path) -> CourseAuthoringManifest:
        """Load one bounded, strict UTF-8 JSON manifest."""

        raw = _read_bounded_path(
            path,
            label="course authoring manifest",
            maximum=_MAX_MANIFEST_BYTES,
        )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CourseAuthoringInventoryError(
                "course authoring manifest must be UTF-8"
            ) from error
        try:
            value = json.loads(text, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, CourseAuthoringInventoryError) as error:
            if isinstance(error, CourseAuthoringInventoryError):
                raise
            raise CourseAuthoringInventoryError(
                "course authoring manifest must be valid JSON"
            ) from error
        data = _require_object(value, label="course authoring manifest")
        _require_exact_fields(data, cls.EXPECTED_FIELDS, label="manifest")
        reference_scope = _require_object(
            data["reference_scope"],
            label="reference_scope",
        )
        _require_exact_fields(
            reference_scope,
            frozenset({"preserved_citekeys", "external_bindings"}),
            label="reference_scope",
        )
        bindings_value = _require_list(
            reference_scope["external_bindings"],
            label="external_bindings",
        )
        bindings = tuple(
            _parse_reference_binding(item, index=index)
            for index, item in enumerate(bindings_value)
        )
        return cls(
            schema_id=_require_string(data["schema_id"], "schema_id"),
            schema_version=_require_integer(
                data["schema_version"],
                "schema_version",
            ),
            course_id=_require_string(data["course_id"], "course_id"),
            title=_require_string(data["title"], "title"),
            root_alias=_require_string(data["root_alias"], "root_alias"),
            main_tex=_require_string(data["main_tex"], "main_tex"),
            notebook_directories=_string_tuple(
                data["notebook_directories"],
                label="notebook_directories",
            ),
            preserved_citekeys=_string_tuple(
                reference_scope["preserved_citekeys"],
                label="preserved_citekeys",
            ),
            external_reference_bindings=bindings,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class SourceFileInventory:
    """Record one root-relative source file and its exact byte identity."""

    path: str
    byte_size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class IncludeInventory:
    """Record one literal root-relative LaTeX include edge."""

    source_path: str
    line: int
    command: str
    target_path: str

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "line": self.line,
            "source_path": self.source_path,
            "target_path": self.target_path,
        }


@dataclass(frozen=True, slots=True)
class StructureInventory:
    """Record one detected LaTeX structural command."""

    source_path: str
    line: int
    kind: str
    title: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "line": self.line,
            "source_path": self.source_path,
            "title": self.title,
        }


@dataclass(frozen=True, slots=True)
class CitationInventory:
    """Record one citation-key occurrence."""

    source_path: str
    line: int
    command: str
    citekey: str

    def as_dict(self) -> dict[str, object]:
        return {
            "citekey": self.citekey,
            "command": self.command,
            "line": self.line,
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class InlineBibliographyInventory:
    """Record one inline bibliography key."""

    source_path: str
    line: int
    citekey: str

    def as_dict(self) -> dict[str, object]:
        return {
            "citekey": self.citekey,
            "line": self.line,
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class CourseSourceInventory:
    """Represent a deterministic observation of one explicit course root."""

    manifest: CourseAuthoringManifest
    sources: tuple[SourceFileInventory, ...]
    includes: tuple[IncludeInventory, ...]
    structure: tuple[StructureInventory, ...]
    citations: tuple[CitationInventory, ...]
    inline_bibliography: tuple[InlineBibliographyInventory, ...]
    bibliography_files: tuple[SourceFileInventory, ...]
    notebooks: tuple[SourceFileInventory, ...]
    missing_preserved_citekeys: tuple[str, ...]
    unplanned_inline_citekeys: tuple[str, ...]
    unresolved_citation_citekeys: tuple[str, ...]
    inventory_id: str = field(init=False)

    def __post_init__(self) -> None:
        payload = self.as_dict(include_identity=False)
        object.__setattr__(
            self,
            "inventory_id",
            _content_identity("course-source-inventory", payload),
        )

    @property
    def reference_scope_status(self) -> str:
        """Return a mechanical result, never an acceptance decision."""

        if (
            self.missing_preserved_citekeys
            or self.unplanned_inline_citekeys
            or self.unresolved_citation_citekeys
        ):
            return "incomplete"
        return "complete"

    def as_dict(self, *, include_identity: bool = True) -> dict[str, object]:
        """Return a path-private deterministic projection."""

        cited_citekeys = tuple(
            dict.fromkeys(item.citekey for item in self.citations)
        )
        inline_citekeys = tuple(
            dict.fromkeys(item.citekey for item in self.inline_bibliography)
        )
        payload: dict[str, object] = {
            "course": {
                "course_id": self.manifest.course_id,
                "root_alias": self.manifest.root_alias,
                "title": self.manifest.title,
            },
            "manifest": {
                "manifest_id": self.manifest.manifest_id,
                "manifest_sha256": self.manifest.manifest_sha256,
                "schema_id": self.manifest.schema_id,
                "schema_version": self.manifest.schema_version,
            },
            "manuscript": {
                "bibliography_files": [
                    item.as_dict() for item in self.bibliography_files
                ],
                "citations": [item.as_dict() for item in self.citations],
                "cited_citekeys": list(cited_citekeys),
                "includes": [item.as_dict() for item in self.includes],
                "inline_bibliography": [
                    item.as_dict() for item in self.inline_bibliography
                ],
                "inline_bibliography_citekeys": list(inline_citekeys),
                "main_tex": self.manifest.main_tex,
                "notebooks": [item.as_dict() for item in self.notebooks],
                "sources": [item.as_dict() for item in self.sources],
                "structure": [item.as_dict() for item in self.structure],
            },
            "reference_scope": {
                "external_bindings": [
                    binding.as_dict()
                    for binding in self.manifest.external_reference_bindings
                ],
                "missing_preserved_citekeys": list(
                    self.missing_preserved_citekeys
                ),
                "planned_citekey_count": len(self.manifest.planned_citekeys),
                "planned_citekeys": list(self.manifest.planned_citekeys),
                "preserved_citekeys": list(self.manifest.preserved_citekeys),
                "status": self.reference_scope_status,
                "unplanned_inline_citekeys": list(
                    self.unplanned_inline_citekeys
                ),
                "unresolved_citation_citekeys": list(
                    self.unresolved_citation_citekeys
                ),
            },
            "schema_id": _INVENTORY_SCHEMA_ID,
            "schema_version": _SCHEMA_VERSION,
        }
        if include_identity:
            payload["inventory_id"] = self.inventory_id
        return payload

    def to_json(self) -> str:
        """Serialize canonical, human-readable JSON with no absolute paths."""

        return (
            json.dumps(
                self.as_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


class CourseSourceInventoryProjector:
    """Read an explicit private root and return a side-effect-free inventory."""

    def project(
        self,
        *,
        manifest: CourseAuthoringManifest,
        course_root: Path,
    ) -> CourseSourceInventory:
        """Inventory the exact source tree named by ``manifest``."""

        root = _authorize_course_root(course_root)
        state = _InventoryState(root=root)
        self._visit_tex(
            relative_path=manifest.main_tex,
            state=state,
            active=(),
        )
        bibliography_files = self._bibliography_files(state=state)
        notebooks = self._notebooks(
            root=root,
            directories=manifest.notebook_directories,
        )
        inline_citekeys = tuple(
            dict.fromkeys(item.citekey for item in state.inline_bibliography)
        )
        cited_citekeys = tuple(
            dict.fromkeys(item.citekey for item in state.citations)
        )
        available_citekeys = set(inline_citekeys) | set(
            citekey
            for binding in manifest.external_reference_bindings
            for citekey in binding.citekeys
        )
        return CourseSourceInventory(
            manifest=manifest,
            sources=tuple(state.sources),
            includes=tuple(state.includes),
            structure=tuple(state.structure),
            citations=tuple(state.citations),
            inline_bibliography=tuple(state.inline_bibliography),
            bibliography_files=bibliography_files,
            notebooks=notebooks,
            missing_preserved_citekeys=tuple(
                citekey
                for citekey in manifest.preserved_citekeys
                if citekey not in inline_citekeys
            ),
            unplanned_inline_citekeys=tuple(
                citekey
                for citekey in inline_citekeys
                if citekey not in manifest.planned_citekeys
            ),
            unresolved_citation_citekeys=tuple(
                citekey
                for citekey in cited_citekeys
                if citekey not in available_citekeys
            ),
        )

    def _visit_tex(
        self,
        *,
        relative_path: str,
        state: _InventoryState,
        active: tuple[str, ...],
    ) -> None:
        normalized = _normalize_tex_path(relative_path)
        if normalized in active:
            raise CourseAuthoringInventoryError(
                f"LaTeX include cycle detected at {normalized}"
            )
        if normalized in state.visited:
            return
        if len(state.sources) >= _MAX_SOURCE_FILES:
            raise CourseAuthoringInventoryError(
                "course source file count exceeds the limit"
            )
        content = _read_course_file(
            root=state.root,
            relative_path=normalized,
            maximum=_MAX_SOURCE_BYTES,
        )
        state.total_source_bytes += len(content)
        if state.total_source_bytes > _MAX_TOTAL_SOURCE_BYTES:
            raise CourseAuthoringInventoryError(
                "aggregate course source bytes exceed the limit"
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CourseAuthoringInventoryError(
                f"course source must be UTF-8: {normalized}"
            ) from error
        if len(text) > _MAX_TEXT_CHARACTERS:
            raise CourseAuthoringInventoryError(
                f"course source text exceeds the character limit: {normalized}"
            )
        state.visited.add(normalized)
        state.sources.append(_file_inventory(normalized, content))
        commands = _latex_commands(text)
        nested_active = active + (normalized,)
        for command in commands:
            if command.name in {"input", "include"}:
                target = _normalize_tex_path(command.argument.strip())
                state.includes.append(
                    IncludeInventory(
                        source_path=normalized,
                        line=command.line,
                        command=command.name,
                        target_path=target,
                    )
                )
                self._visit_tex(
                    relative_path=target,
                    state=state,
                    active=nested_active,
                )
            elif command.name in _HEADING_COMMANDS:
                state.structure.append(
                    StructureInventory(
                        source_path=normalized,
                        line=command.line,
                        kind=command.name,
                        title=command.argument.strip(),
                    )
                )
            elif command.name in _CITATION_COMMANDS:
                for citekey in _split_citekeys(command.argument):
                    state.citations.append(
                        CitationInventory(
                            source_path=normalized,
                            line=command.line,
                            command=command.name,
                            citekey=citekey,
                        )
                    )
            elif command.name == "bibitem":
                citekey = command.argument.strip()
                _validate_citekey(citekey, label="inline bibliography citekey")
                state.inline_bibliography.append(
                    InlineBibliographyInventory(
                        source_path=normalized,
                        line=command.line,
                        citekey=citekey,
                    )
                )
            elif command.name in {"bibliography", "addbibresource"}:
                values = (
                    command.argument.split(",")
                    if command.name == "bibliography"
                    else [command.argument]
                )
                state.bibliography_declarations.extend(
                    _normalize_bibliography_path(value.strip())
                    for value in values
                )

    @staticmethod
    def _bibliography_files(
        *,
        state: _InventoryState,
    ) -> tuple[SourceFileInventory, ...]:
        ordered = tuple(dict.fromkeys(state.bibliography_declarations))
        return tuple(
            _file_inventory(
                relative_path,
                _read_course_file(
                    root=state.root,
                    relative_path=relative_path,
                    maximum=_MAX_SOURCE_BYTES,
                ),
            )
            for relative_path in ordered
        )

    @staticmethod
    def _notebooks(
        *,
        root: Path,
        directories: tuple[str, ...],
    ) -> tuple[SourceFileInventory, ...]:
        relative_files: list[str] = []
        for relative_directory in directories:
            directory = _course_path(root, relative_directory)
            if not directory.exists():
                raise CourseAuthoringInventoryError(
                    f"notebook directory does not exist: {relative_directory}"
                )
            _require_no_symlink_path(root, relative_directory)
            if not directory.is_dir():
                raise CourseAuthoringInventoryError(
                    f"notebook path is not a directory: {relative_directory}"
                )
            for candidate in directory.rglob("*.ipynb"):
                relative = candidate.relative_to(root).as_posix()
                _safe_relative_path(relative, label="notebook path")
                _require_no_symlink_path(root, relative)
                relative_files.append(relative)
                if len(relative_files) > _MAX_NOTEBOOK_FILES:
                    raise CourseAuthoringInventoryError(
                        "notebook file count exceeds the limit"
                    )
        if len(relative_files) != len(set(relative_files)):
            raise CourseAuthoringInventoryError(
                "notebook directories select duplicate files"
            )
        return tuple(
            _file_inventory(
                relative,
                _read_course_file(
                    root=root,
                    relative_path=relative,
                    maximum=_MAX_SOURCE_BYTES,
                ),
            )
            for relative in sorted(relative_files)
        )


@dataclass(slots=True)
class _InventoryState:
    root: Path
    sources: list[SourceFileInventory] = field(default_factory=list)
    includes: list[IncludeInventory] = field(default_factory=list)
    structure: list[StructureInventory] = field(default_factory=list)
    citations: list[CitationInventory] = field(default_factory=list)
    inline_bibliography: list[InlineBibliographyInventory] = field(
        default_factory=list
    )
    bibliography_declarations: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    total_source_bytes: int = 0


@dataclass(frozen=True, slots=True)
class _LatexCommand:
    name: str
    argument: str
    line: int


def _latex_commands(text: str) -> tuple[_LatexCommand, ...]:
    uncommented = _strip_latex_comments(text)
    commands: list[_LatexCommand] = []
    for match in _COMMAND.finditer(uncommented):
        cursor = match.end()
        while cursor < len(uncommented) and uncommented[cursor].isspace():
            cursor += 1
        while cursor < len(uncommented) and uncommented[cursor] == "[":
            cursor = _balanced_end(uncommented, cursor, "[", "]") + 1
            while cursor < len(uncommented) and uncommented[cursor].isspace():
                cursor += 1
        if cursor >= len(uncommented) or uncommented[cursor] != "{":
            continue
        end = _balanced_end(uncommented, cursor, "{", "}")
        commands.append(
            _LatexCommand(
                name=match.group("name"),
                argument=uncommented[cursor + 1 : end],
                line=uncommented.count("\n", 0, match.start()) + 1,
            )
        )
    return tuple(commands)


def _balanced_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
    raise CourseAuthoringInventoryError("unterminated LaTeX command argument")


def _strip_latex_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        comment_at: int | None = None
        for index, character in enumerate(line):
            if character != "%":
                continue
            backslashes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2 == 0:
                comment_at = index
                break
        if comment_at is None:
            lines.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        lines.append(line[:comment_at] + newline)
    return "".join(lines)


def _authorize_course_root(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise CourseAuthoringInventoryError("course root must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CourseAuthoringInventoryError(
            "course root does not exist"
        ) from error
    if not resolved.is_dir():
        raise CourseAuthoringInventoryError("course root must be a directory")
    return resolved


def _course_path(root: Path, relative_path: str) -> Path:
    safe = _safe_relative_path(relative_path, label="course path")
    return root.joinpath(*safe.parts)


def _require_no_symlink_path(root: Path, relative_path: str) -> None:
    safe = _safe_relative_path(relative_path, label="course path")
    candidate = root
    for part in safe.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise CourseAuthoringInventoryError(
                f"course path must not contain symlinks: {relative_path}"
            )


def _read_course_file(
    *,
    root: Path,
    relative_path: str,
    maximum: int,
) -> bytes:
    candidate = _course_path(root, relative_path)
    _require_no_symlink_path(root, relative_path)
    try:
        before = candidate.stat()
    except OSError as error:
        raise CourseAuthoringInventoryError(
            f"course file does not exist: {relative_path}"
        ) from error
    if not stat.S_ISREG(before.st_mode):
        raise CourseAuthoringInventoryError(
            f"course path is not a regular file: {relative_path}"
        )
    if before.st_size > maximum:
        raise CourseAuthoringInventoryError(
            f"course file exceeds its byte limit: {relative_path}"
        )
    try:
        with candidate.open("rb") as stream:
            content = stream.read(maximum + 1)
    except OSError as error:
        raise CourseAuthoringInventoryError(
            f"course file could not be read: {relative_path}"
        ) from error
    if len(content) > maximum:
        raise CourseAuthoringInventoryError(
            f"course file exceeds its byte limit: {relative_path}"
        )
    after = candidate.stat()
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(content) != after.st_size:
        raise CourseAuthoringInventoryError(
            f"course file changed while being read: {relative_path}"
        )
    return content


def _read_bounded_path(path: Path, *, label: str, maximum: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise CourseAuthoringInventoryError(
            f"{label} does not exist"
        ) from error
    if not path.is_file():
        raise CourseAuthoringInventoryError(f"{label} must be a regular file")
    if size > maximum:
        raise CourseAuthoringInventoryError(f"{label} exceeds its byte limit")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise CourseAuthoringInventoryError(
            f"{label} could not be read"
        ) from error
    if len(content) > maximum:
        raise CourseAuthoringInventoryError(f"{label} exceeds its byte limit")
    return content


def _file_inventory(relative_path: str, content: bytes) -> SourceFileInventory:
    return SourceFileInventory(
        path=relative_path,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _normalize_tex_path(value: str) -> str:
    candidate = value if PurePosixPath(value).suffix else f"{value}.tex"
    return _safe_relative_path(
        candidate,
        label="LaTeX include path",
        suffix=".tex",
    ).as_posix()


def _normalize_bibliography_path(value: str) -> str:
    candidate = value if PurePosixPath(value).suffix else f"{value}.bib"
    return _safe_relative_path(
        candidate,
        label="bibliography path",
        suffix=".bib",
    ).as_posix()


def _safe_relative_path(
    value: str,
    *,
    label: str,
    suffix: str | None = None,
) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or len(value) > 1_000
    ):
        raise CourseAuthoringInventoryError(
            f"{label} must be a bounded POSIX relative path"
        )
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or re.match(r"^[A-Za-z]:", value) is not None
        or not candidate.parts
        or value != candidate.as_posix()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise CourseAuthoringInventoryError(
            f"{label} must be a bounded POSIX relative path"
        )
    if suffix is not None and candidate.suffix != suffix:
        raise CourseAuthoringInventoryError(f"{label} must end with {suffix}")
    return candidate


def _validate_relative_paths(values: tuple[str, ...], *, label: str) -> None:
    if not values:
        raise CourseAuthoringInventoryError(f"{label} must not be empty")
    for value in values:
        _safe_relative_path(value, label=label)
    if len(values) != len(set(values)):
        raise CourseAuthoringInventoryError(f"{label} must be unique")


def _split_citekeys(value: str) -> tuple[str, ...]:
    citekeys = tuple(item.strip() for item in value.split(","))
    _validate_citekeys(citekeys, label="citation citekeys")
    return citekeys


def _validate_citekeys(values: tuple[str, ...], *, label: str) -> None:
    if not isinstance(values, tuple) or not values:
        raise CourseAuthoringInventoryError(f"{label} must be a nonempty tuple")
    for value in values:
        _validate_citekey(value, label=label)
    if len(values) != len(set(values)):
        raise CourseAuthoringInventoryError(f"{label} must be unique")


def _validate_citekey(value: str, *, label: str) -> None:
    if not isinstance(value, str) or _CITEKEY.fullmatch(value) is None:
        raise CourseAuthoringInventoryError(
            f"{label} contains an invalid citekey"
        )


def _validate_trimmed(value: str, label: str, *, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise CourseAuthoringInventoryError(
            f"{label} must contain 1 to {maximum} trimmed characters"
        )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CourseAuthoringInventoryError(
                f"course authoring manifest repeats field: {key}"
            )
        result[key] = value
    return result


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise CourseAuthoringInventoryError(f"{label} must be a JSON object")
    return value


def _require_list(value: Any, *, label: str) -> list[Any]:
    if type(value) is not list:
        raise CourseAuthoringInventoryError(f"{label} must be a JSON array")
    return value


def _require_exact_fields(
    value: dict[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise CourseAuthoringInventoryError(
            f"{label} fields differ: missing={missing}, unknown={unknown}"
        )


def _require_string(value: Any, label: str) -> str:
    if type(value) is not str:
        raise CourseAuthoringInventoryError(f"{label} must be a string")
    return value


def _require_integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise CourseAuthoringInventoryError(f"{label} must be an integer")
    return value


def _string_tuple(value: Any, *, label: str) -> tuple[str, ...]:
    values = _require_list(value, label=label)
    if any(type(item) is not str for item in values):
        raise CourseAuthoringInventoryError(f"{label} must contain strings")
    return tuple(values)


def _parse_reference_binding(
    value: Any,
    *,
    index: int,
) -> ExternalReferenceBinding:
    data = _require_object(value, label=f"external_bindings[{index}]")
    expected = frozenset(
        {
            "owner",
            "source_id",
            "source_revision",
            "identity_status",
            "citekey_status",
            "citekeys",
        }
    )
    _require_exact_fields(
        data,
        expected,
        label=f"external_bindings[{index}]",
    )
    return ExternalReferenceBinding(
        owner=_require_string(data["owner"], "owner"),
        source_id=_require_string(data["source_id"], "source_id"),
        source_revision=_require_string(
            data["source_revision"],
            "source_revision",
        ),
        identity_status=_require_string(
            data["identity_status"],
            "identity_status",
        ),
        citekey_status=_require_string(
            data["citekey_status"],
            "citekey_status",
        ),
        citekeys=_string_tuple(data["citekeys"], label="citekeys"),
    )


def _content_identity(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(encoded).hexdigest()}"
