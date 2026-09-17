"""Default-deny sanitization for legacy course collections."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any


class SanitizationError(RuntimeError):
    """Raised when a source cannot be sanitized safely."""


@dataclass(frozen=True)
class ExclusionRule:
    """Explain why source paths matching a glob are excluded."""

    pattern: str
    reason: str


@dataclass(frozen=True)
class LiteralRedaction:
    """Replace a known identifying literal in retained text."""

    label: str
    value: str


@dataclass(frozen=True)
class ProvenanceEntry:
    """One BibLaTeX entry for an intellectual source in the collection."""

    key: str
    entry_type: str
    title: str
    source_path: str
    author: str = ""
    year: str = ""
    note: str = ""


@dataclass(frozen=True)
class SanitizationPolicy:
    """Explicit allowlist and classification rules for one course."""

    institution: str
    course_code: str
    course_title: str
    source_alias: str
    retained_text: tuple[str, ...]
    retained_pdfs: tuple[str, ...]
    exclusions: tuple[ExclusionRule, ...]
    literal_redactions: tuple[LiteralRedaction, ...] = ()
    provenance: tuple[ProvenanceEntry, ...] = ()
    copy_excluded: bool = False

    def __post_init__(self) -> None:
        _require_safe_source_alias(self.source_alias)

    @classmethod
    def from_json(cls, path: Path) -> SanitizationPolicy:
        """Load and validate a version-one policy document."""
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise SanitizationError("policy must use schema_version 1")

        required_strings = (
            "institution",
            "course_code",
            "course_title",
            "source_alias",
        )
        for field in required_strings:
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise SanitizationError(
                    f"policy field {field!r} must be a string"
                )

        def string_tuple(field: str) -> tuple[str, ...]:
            value = data.get(field, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise SanitizationError(
                    f"policy field {field!r} must be a string list"
                )
            return tuple(value)

        exclusions = _parse_exclusions(data.get("exclusions", []))
        redactions = _parse_redactions(data.get("literal_redactions", []))
        provenance = _parse_provenance(data.get("provenance", []))
        copy_excluded = data.get("copy_excluded", False)
        if not isinstance(copy_excluded, bool):
            raise SanitizationError("policy copy_excluded must be boolean")
        return cls(
            institution=data["institution"],
            course_code=data["course_code"],
            course_title=data["course_title"],
            source_alias=data["source_alias"],
            retained_text=string_tuple("retained_text"),
            retained_pdfs=string_tuple("retained_pdfs"),
            exclusions=tuple(exclusions),
            literal_redactions=tuple(redactions),
            provenance=tuple(provenance),
            copy_excluded=copy_excluded,
        )


@dataclass(frozen=True)
class Classification:
    """Audit decision for one source file."""

    source_path: str
    source_sha256: str
    source_bytes: int
    action: str
    reason: str
    destination: str


@dataclass(frozen=True)
class RedactionRecord:
    """Count one kind of redaction in a retained file."""

    source: str
    destination: str
    label: str
    count: int


@dataclass(frozen=True)
class SanitizationResult:
    """Summary of one completed sanitization run."""

    reviewed: int
    retained: int
    excluded: int
    redactions: int
    destination: Path


class CourseSanitizer:
    """Produce an auditable sanitized archive from an explicit policy."""

    def __init__(self, *, ghostscript: str = "gs") -> None:
        self._ghostscript = ghostscript

    def sanitize(
        self,
        source: Path,
        destination: Path,
        policy: SanitizationPolicy,
        *,
        replace: bool = False,
    ) -> SanitizationResult:
        """Build in staging, then atomically replace the destination."""
        source = source.expanduser().resolve()
        destination = destination.expanduser().resolve()
        if not source.is_dir():
            raise SanitizationError(
                f"source directory does not exist: {source}"
            )
        if destination == source or source in destination.parents:
            raise SanitizationError("destination must be outside the source")
        if destination.exists() and not replace:
            raise SanitizationError(
                "destination exists; pass replace=True to replace it"
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.sanitizing-",
                dir=destination.parent,
            )
        )
        try:
            result = self._build(source, staging, policy)
            self._install(staging, destination, replace=replace)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return SanitizationResult(
            reviewed=result.reviewed,
            retained=result.retained,
            excluded=result.excluded,
            redactions=result.redactions,
            destination=destination,
        )

    def _build(
        self,
        source: Path,
        staging: Path,
        policy: SanitizationPolicy,
    ) -> SanitizationResult:
        classifications: list[Classification] = []
        redactions: list[RedactionRecord] = []
        audit_sources: dict[str, str] = {}

        for source_path in _source_entries(source):
            relative = source_path.relative_to(source)
            relative_text = relative.as_posix()
            audit_path = _sanitize_path(relative_text, policy)
            previous_source = audit_sources.get(audit_path)
            if previous_source is not None:
                raise SanitizationError(
                    "source paths become ambiguous after redaction: "
                    f"{audit_path}"
                )
            audit_sources[audit_path] = relative_text
            if source_path.is_symlink():
                classifications.append(
                    Classification(
                        source_path=audit_path,
                        source_sha256="",
                        source_bytes=source_path.lstat().st_size,
                        action="excluded",
                        reason=(
                            "symbolic link excluded without following target"
                        ),
                        destination="",
                    )
                )
                continue

            source_hash = _sha256(source_path)
            source_bytes = source_path.stat().st_size

            if _matches(relative_text, policy.retained_text):
                destination = Path("student-work") / Path(audit_path)
                changes = self._sanitize_text(
                    source_path,
                    staging / destination,
                    audit_path,
                    destination.as_posix(),
                    policy,
                )
                redactions.extend(changes)
                classification = Classification(
                    source_path=audit_path,
                    source_sha256=source_hash,
                    source_bytes=source_bytes,
                    action="retained-sanitized",
                    reason="allowlisted student-authored text or source code",
                    destination=destination.as_posix(),
                )
            elif _matches(relative_text, policy.retained_pdfs):
                if source_path.suffix.lower() != ".pdf":
                    raise SanitizationError(
                        f"allowlisted PDF is not a PDF: {audit_path}"
                    )
                sanitized_stem = Path(audit_path).stem
                destination = (
                    Path("course-records")
                    / f"{sanitized_stem}.sanitized.pdf"
                )
                self._sanitize_pdf(
                    source_path,
                    staging / destination,
                    policy,
                )
                classification = Classification(
                    source_path=audit_path,
                    source_sha256=source_hash,
                    source_bytes=source_bytes,
                    action="retained-sanitized",
                    reason="allowlisted PDF rewritten with metadata cleared",
                    destination=destination.as_posix(),
                )
            else:
                private_destination = ""
                if policy.copy_excluded:
                    private_path = self._copy_private_source(
                        source_path,
                        staging,
                        Path(audit_path),
                        source_hash,
                    )
                    private_destination = private_path.as_posix()
                classification = Classification(
                    source_path=audit_path,
                    source_sha256=source_hash,
                    source_bytes=source_bytes,
                    action="excluded",
                    reason=_exclusion_reason(relative_text, policy.exclusions),
                    destination=private_destination,
                )
            classifications.append(classification)

        self._write_audit(staging, classifications, redactions, policy)
        self._write_provenance(staging, classifications, policy)
        self._write_readme(staging, policy)
        self._write_manifest(staging)
        retained = sum(
            item.action == "retained-sanitized" for item in classifications
        )
        return SanitizationResult(
            reviewed=len(classifications),
            retained=retained,
            excluded=len(classifications) - retained,
            redactions=sum(item.count for item in redactions),
            destination=staging,
        )

    def _copy_private_source(
        self,
        source: Path,
        staging: Path,
        relative: Path,
        source_hash: str,
    ) -> Path:
        destination = Path("private-sources") / relative
        output = staging / destination
        if output.exists():
            output = _collision_path(output, source_hash)
            destination = output.relative_to(staging)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        _clear_xattrs(output)
        if _sha256(output) != source_hash:
            raise SanitizationError(
                f"private source checksum mismatch: {relative}"
            )
        return destination

    def _sanitize_text(
        self,
        source: Path,
        destination: Path,
        source_name: str,
        destination_name: str,
        policy: SanitizationPolicy,
    ) -> list[RedactionRecord]:
        raw = source.read_bytes()
        if b"\x00" in raw:
            raise SanitizationError(
                f"allowlisted text appears to be binary: {source_name}"
            )
        try:
            text = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
            encoding = "latin-1"

        records: list[RedactionRecord] = []
        replacements = [
            *(
                (item.label, re.compile(re.escape(item.value), re.IGNORECASE))
                for item in policy.literal_redactions
            ),
            ("email address", _EMAIL_PATTERN),
            ("phone number", _PHONE_PATTERN),
        ]
        for label, pattern in replacements:
            text, count = pattern.subn(f"[REDACTED {label.upper()}]", text)
            if count:
                records.append(
                    RedactionRecord(
                        source=source_name,
                        destination=destination_name,
                        label=label,
                        count=count,
                    )
                )

        text, count = _STUDENT_ID_PATTERN.subn(
            r"\1[REDACTED]",
            text,
        )
        if count:
            records.append(
                RedactionRecord(
                    source=source_name,
                    destination=destination_name,
                    label="student identifier",
                    count=count,
                )
            )
        if _SECRET_PATTERN.search(text):
            raise SanitizationError(
                f"possible secret requires manual review: {source_name}"
            )

        _ensure_available(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode(encoding))
        _clear_xattrs(destination)
        return records

    def _sanitize_pdf(
        self,
        source: Path,
        destination: Path,
        policy: SanitizationPolicy,
    ) -> None:
        _ensure_available(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._ghostscript,
            "-q",
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.7",
            f"-sOutputFile={destination}",
            "-f",
            str(source),
            "-c",
            "[/Title () /Author () /Subject () /Keywords () "
            "/Creator () /Producer () /DOCINFO pdfmark",
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as error:
            raise SanitizationError(
                f"Ghostscript executable not found: {self._ghostscript}"
            ) from error
        except subprocess.CalledProcessError as error:
            raise SanitizationError(
                f"Ghostscript failed for {source.name}: {error.stderr.strip()}"
            ) from error
        _clear_xattrs(destination)

        text = self._extract_pdf_text(destination)
        findings = _pii_findings(text, policy.literal_redactions)
        if findings:
            destination.unlink(missing_ok=True)
            labels = ", ".join(sorted(findings))
            raise SanitizationError(
                f"visible PDF content requires manual redaction: {labels}"
            )

    def _extract_pdf_text(self, source: Path) -> str:
        with tempfile.NamedTemporaryFile(suffix=".txt") as output:
            command = [
                self._ghostscript,
                "-q",
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=txtwrite",
                f"-sOutputFile={output.name}",
                str(source),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as error:
                raise SanitizationError(
                    f"could not inspect PDF text: {error.stderr.strip()}"
                ) from error
            return Path(output.name).read_text(
                encoding="utf-8",
                errors="replace",
            )

    def _write_audit(
        self,
        staging: Path,
        classifications: list[Classification],
        redactions: list[RedactionRecord],
        policy: SanitizationPolicy,
    ) -> None:
        audit = staging / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        with (audit / "classification.csv").open(
            "w",
            newline="",
            encoding="utf-8",
        ) as stream:
            fields = tuple(Classification.__dataclass_fields__)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(asdict(item) for item in classifications)

        retained = sum(
            item.action == "retained-sanitized" for item in classifications
        )
        report = {
            "schema_version": 1,
            "institution": policy.institution,
            "course_code": policy.course_code,
            "course_title": policy.course_title,
            "sanitized_at": datetime.now(UTC).isoformat(),
            "source_alias": policy.source_alias,
            "source_files_reviewed": len(classifications),
            "retained_files": retained,
            "excluded_files": len(classifications) - retained,
            "redactions": [asdict(item) for item in redactions],
            "redaction_labels": sorted(
                {item.label for item in policy.literal_redactions}
            ),
            "policy": {
                "default": "exclude",
                "retained_text": list(policy.retained_text),
                "retained_pdfs": list(policy.retained_pdfs),
                "exclusions": [asdict(item) for item in policy.exclusions],
                "copy_excluded": policy.copy_excluded,
                "provenance_entries": len(policy.provenance),
                "absolute_source_paths_recorded": False,
            },
        }
        private_manifest = [
            f"{item.source_sha256}  {item.destination}"
            for item in classifications
            if item.action == "excluded" and item.destination
        ]
        if private_manifest:
            (audit / "private-sources.sha256").write_text(
                "\n".join(private_manifest) + "\n",
                encoding="utf-8",
            )
        (audit / "sanitization-report.json").write_text(
            json.dumps(report, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_provenance(
        self,
        staging: Path,
        classifications: list[Classification],
        policy: SanitizationPolicy,
    ) -> None:
        by_path = {item.source_path: item for item in classifications}
        entries = []
        for entry in policy.provenance:
            audit_path = _sanitize_path(entry.source_path, policy)
            classification = by_path.get(audit_path)
            if classification is None:
                raise SanitizationError(
                    f"provenance source was not found: {audit_path}"
                )
            artifact_hash = ""
            if classification.destination:
                artifact_hash = _sha256(
                    staging / classification.destination
                )
            fields = [
                ("title", entry.title),
                ("author", entry.author),
                ("year", entry.year),
                ("institution", policy.institution),
                ("course", policy.course_code),
                ("file", classification.destination),
                ("sourcefile", audit_path),
                ("sourcesha256", classification.source_sha256),
                (
                    "artifactsha256",
                    artifact_hash
                    if artifact_hash != classification.source_sha256
                    else "",
                ),
                ("note", entry.note),
                ("keywords", "legacy-source, provenance"),
            ]
            rendered = [f"@{entry.entry_type}{{{entry.key},"]
            rendered.extend(
                f"  {name} = {{{_bib_escape(value)}}},"
                for name, value in fields
                if value
            )
            rendered.append("}")
            entries.append("\n".join(rendered))
        if entries:
            (staging / "provenance.bib").write_text(
                "\n\n".join(entries) + "\n",
                encoding="utf-8",
            )

    def _write_readme(
        self,
        staging: Path,
        policy: SanitizationPolicy,
    ) -> None:
        (staging / "README.md").write_text(
            "# Sanitized legacy archive\n\n"
            f"Institution: {policy.institution}  \n"
            f"Course: `{policy.course_code}` — {policy.course_title}  \n"
            f"Source alias: `{policy.source_alias}`\n\n"
            "This default-deny archive retains only files explicitly "
            "allowlisted by the course sanitization policy. Unknown, "
            "third-party, generated, binary, and rights-unclear files remain "
            "excluded from Git. When configured, local reference copies live "
            "under the ignored `private-sources/` tree. See `audit/` for every "
            "classification decision and `provenance.bib` for intellectual "
            "source citations.\n\n"
            "No blanket license is granted for historical course material.\n",
            encoding="utf-8",
        )

    def _write_manifest(self, staging: Path) -> None:
        lines = []
        for path in sorted(
            item
            for item in staging.rglob("*")
            if item.is_file()
            and item.name != "MANIFEST.sha256"
            and "private-sources" not in item.relative_to(staging).parts
        ):
            lines.append(f"{_sha256(path)}  {path.relative_to(staging)}")
        (staging / "MANIFEST.sha256").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    def _install(
        self,
        staging: Path,
        destination: Path,
        *,
        replace: bool,
    ) -> None:
        previous: Path | None = None
        if destination.exists():
            if not replace:
                raise SanitizationError("destination already exists")
            previous = destination.with_name(
                f".{destination.name}.previous-{os.getpid()}"
            )
            if previous.exists():
                shutil.rmtree(previous)
            destination.rename(previous)
        try:
            staging.rename(destination)
        except Exception:
            if previous is not None and previous.exists():
                previous.rename(destination)
            raise
        if previous is not None:
            shutil.rmtree(previous)


_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
)
_STUDENT_ID_PATTERN = re.compile(
    r"(?i)(student\s*(?:id|number)\s*[:#]?\s*)\d{5,}"
)
_LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)


def _require_safe_source_alias(value: str) -> None:
    alias = value.strip()
    if (
        not alias
        or Path(alias).is_absolute()
        or PureWindowsPath(alias).is_absolute()
        or alias.startswith("~")
    ):
        raise SanitizationError(
            "policy source_alias must be a non-home-relative alias"
        )


def _parse_exclusions(value: Any) -> list[ExclusionRule]:
    if not isinstance(value, list):
        raise SanitizationError("policy exclusions must be a list")
    records = []
    for item in value:
        if not isinstance(item, dict):
            raise SanitizationError("invalid ExclusionRule policy record")
        pattern = item.get("pattern")
        reason = item.get("reason")
        if not isinstance(pattern, str) or not isinstance(reason, str):
            raise SanitizationError("invalid ExclusionRule policy record")
        if not pattern or not reason:
            raise SanitizationError("invalid ExclusionRule policy record")
        records.append(ExclusionRule(pattern=pattern, reason=reason))
    return records


def _parse_redactions(value: Any) -> list[LiteralRedaction]:
    if not isinstance(value, list):
        raise SanitizationError("policy literal_redactions must be a list")
    records = []
    for item in value:
        if not isinstance(item, dict):
            raise SanitizationError("invalid LiteralRedaction policy record")
        label = item.get("label")
        redaction_value = item.get("value")
        if not isinstance(label, str) or not isinstance(
            redaction_value,
            str,
        ):
            raise SanitizationError("invalid LiteralRedaction policy record")
        if not label or not redaction_value:
            raise SanitizationError("invalid LiteralRedaction policy record")
        records.append(
            LiteralRedaction(label=label, value=redaction_value)
        )
    return records


def _parse_provenance(value: Any) -> list[ProvenanceEntry]:
    if not isinstance(value, list):
        raise SanitizationError("policy provenance must be a list")
    records = []
    keys = set()
    for item in value:
        if not isinstance(item, dict):
            raise SanitizationError("invalid ProvenanceEntry policy record")
        required = ("key", "entry_type", "title", "source_path")
        if not all(
            isinstance(item.get(field), str) and item[field]
            for field in required
        ):
            raise SanitizationError("invalid ProvenanceEntry policy record")
        key = item["key"]
        entry_type = item["entry_type"]
        source_path = Path(item["source_path"])
        if not re.fullmatch(r"[A-Za-z0-9_.:+-]+", key):
            raise SanitizationError(f"invalid BibLaTeX key: {key}")
        if key in keys:
            raise SanitizationError(f"duplicate BibLaTeX key: {key}")
        if not re.fullmatch(r"[A-Za-z]+", entry_type):
            raise SanitizationError(
                f"invalid BibLaTeX entry type: {entry_type}"
            )
        if source_path.is_absolute() or ".." in source_path.parts:
            raise SanitizationError("provenance source_path must be relative")
        optional = {}
        for field in ("author", "year", "note"):
            field_value = item.get(field, "")
            if not isinstance(field_value, str):
                raise SanitizationError(
                    f"provenance field {field!r} must be a string"
                )
            optional[field] = field_value
        records.append(
            ProvenanceEntry(
                key=key,
                entry_type=entry_type,
                title=item["title"],
                source_path=item["source_path"],
                author=optional["author"],
                year=optional["year"],
                note=optional["note"],
            )
        )
        keys.add(key)
    return records


def _source_entries(source: Path) -> Iterable[Path]:
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or path.is_file():
            yield path


def _sanitize_path(path: str, policy: SanitizationPolicy) -> str:
    sanitized = path
    replacements = [
        *(
            (re.compile(re.escape(item.value), re.IGNORECASE), item.label)
            for item in policy.literal_redactions
        ),
        (_EMAIL_PATTERN, "email-address"),
        (_PHONE_PATTERN, "phone-number"),
    ]
    for pattern, label in replacements:
        path_label = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")
        sanitized = pattern.sub(
            f"[REDACTED-{path_label.upper()}]",
            sanitized,
        )
    return _LONG_NUMBER_PATTERN.sub(_sanitize_long_number, sanitized)


def _sanitize_long_number(match: re.Match[str]) -> str:
    value = match.group()
    if len(value) == 8:
        try:
            parsed = datetime.strptime(value, "%Y%m%d")
        except ValueError:
            pass
        else:
            if 1900 <= parsed.year <= 2099:
                return value
    return "[REDACTED-LONG-NUMBER]"


def _collision_path(path: Path, source_hash: str) -> Path:
    candidate = path.with_name(
        f"{path.stem}-{source_hash[:12]}{path.suffix}"
    )
    if candidate.exists():
        raise SanitizationError(
            f"private source destination collision: {candidate.name}"
        )
    return candidate


def _bib_escape(value: str) -> str:
    return value.replace("{", r"\{").replace("}", r"\}")


def _ensure_available(destination: Path) -> None:
    if destination.exists():
        raise SanitizationError(
            f"sanitized destination collision: {destination.name}"
        )


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _exclusion_reason(path: str, rules: tuple[ExclusionRule, ...]) -> str:
    for rule in rules:
        if fnmatch.fnmatchcase(path, rule.pattern):
            return rule.reason
    return "not allowlisted; ownership or privacy status requires review"


def _pii_findings(
    text: str,
    literal_redactions: tuple[LiteralRedaction, ...],
) -> set[str]:
    findings = {
        item.label
        for item in literal_redactions
        if re.search(re.escape(item.value), text, re.IGNORECASE)
    }
    patterns = (
        ("email address", _EMAIL_PATTERN),
        ("phone number", _PHONE_PATTERN),
        ("student identifier", _STUDENT_ID_PATTERN),
        ("possible secret", _SECRET_PATTERN),
    )
    findings.update(
        label for label, pattern in patterns if pattern.search(text)
    )
    return findings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _clear_xattrs(path: Path) -> None:
    listxattr = getattr(os, "listxattr", None)
    removexattr = getattr(os, "removexattr", None)
    if listxattr is None or removexattr is None:
        return
    for attribute in listxattr(path):
        removexattr(path, attribute)
