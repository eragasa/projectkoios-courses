"""Race-resistant, bounded archive operations for proposal adapters."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .models import ProblemSolvingError

_READ_CHUNK_BYTES = 8192
_MAXIMUM_CHILD_NAME_BYTES = 240


@dataclass(frozen=True, slots=True)
class _FileMetadata:
    device: int
    inode: int
    mode: int
    link_count: int
    user_id: int
    group_id: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def capture(cls, value: os.stat_result) -> _FileMetadata:
        return cls(
            device=value.st_dev,
            inode=value.st_ino,
            mode=value.st_mode,
            link_count=value.st_nlink,
            user_id=value.st_uid,
            group_id=value.st_gid,
            size=value.st_size,
            modified_ns=value.st_mtime_ns,
            changed_ns=value.st_ctime_ns,
        )

    def publication_fields(self) -> tuple[int, ...]:
        """Return fields that link and unlink operations cannot change."""
        return (
            self.device,
            self.inode,
            self.mode,
            self.user_id,
            self.group_id,
            self.size,
            self.modified_ns,
        )


class ProposalArchive:
    """Operate only on direct children of one protected archive directory."""

    def __init__(self, directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            initial_stat = os.stat(directory, follow_symlinks=False)
            descriptor = os.open(directory, flags)
        except (FileNotFoundError, OSError) as error:
            raise ProblemSolvingError(
                "proposal archive directory is unavailable or symlinked"
            ) from error
        try:
            opened_stat = os.fstat(descriptor)
            root = directory.resolve(strict=True)
            resolved_stat = root.stat()
            identities = {
                (initial_stat.st_dev, initial_stat.st_ino),
                (opened_stat.st_dev, opened_stat.st_ino),
                (resolved_stat.st_dev, resolved_stat.st_ino),
            }
            if not stat.S_ISDIR(opened_stat.st_mode) or len(identities) != 1:
                raise ProblemSolvingError(
                    "proposal archive directory changed or is symlinked"
                )
            if opened_stat.st_mode & 0o077:
                raise ProblemSolvingError(
                    "proposal archive directory must have mode 0700"
                )
            self._root = root
            self._root_identity = (
                opened_stat.st_dev,
                opened_stat.st_ino,
            )
        finally:
            os.close(descriptor)

    @property
    def root(self) -> Path:
        return self._root

    def child(self, name: str) -> Path:
        try:
            encoded = name.encode("ascii")
        except UnicodeEncodeError as error:
            raise ProblemSolvingError(
                "proposal archive child name must be ASCII"
            ) from error
        if (
            not encoded
            or len(encoded) > _MAXIMUM_CHILD_NAME_BYTES
            or name in {".", ".."}
            or Path(name).name != name
            or "/" in name
            or "\\" in name
        ):
            raise ProblemSolvingError("proposal archive child name is invalid")
        return self._root / name

    def read_optional(self, path: Path, *, maximum_bytes: int) -> bytes | None:
        if maximum_bytes < 1:
            raise ProblemSolvingError("archive read bound must be positive")
        directory_fd = self._open_directory()
        try:
            self._validate_child(path)
            return self._read_existing(
                directory_fd,
                path.name,
                maximum_bytes=maximum_bytes,
            )
        finally:
            os.close(directory_fd)

    def write_or_confirm(self, path: Path, payload: bytes) -> None:
        directory_fd = self._open_directory()
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            self._validate_child(path)
            self._validate_child(temporary)
            existing = self._read_existing(
                directory_fd,
                path.name,
                maximum_bytes=max(1, len(payload)),
            )
            if existing is not None:
                if existing != payload:
                    raise ProblemSolvingError(
                        f"proposal archive collision: {path.name}"
                    )
                return
            if self._entry_exists(directory_fd, temporary.name):
                raise ProblemSolvingError(
                    f"proposal temporary collision: {temporary.name}"
                )

            # Resolve and contain both names again immediately before creation.
            self._validate_child(path)
            self._validate_child(temporary)
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(
                    temporary.name,
                    flags,
                    0o600,
                    dir_fd=directory_fd,
                )
            except OSError as error:
                raise ProblemSolvingError(
                    f"proposal temporary collision: {temporary.name}"
                ) from error
            temporary_created = True
            published = False
            publication_verified = False
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                written = 0
                while written < len(view):
                    count = os.write(descriptor, view[written:])
                    if count < 1:
                        raise ProblemSolvingError(
                            "proposal temporary write made no progress"
                        )
                    written += count
                os.fsync(descriptor)

                # No unchecked name resolution may occur between this payload
                # verification and collision-preserving publication.
                self._validate_child(path)
                self._validate_child(temporary)
                expected_digest = hashlib.sha256(payload).digest()
                prepublication = self._verify_named_payload(
                    directory_fd,
                    temporary.name,
                    descriptor,
                    payload=payload,
                    expected_digest=expected_digest,
                )
                if (
                    stat.S_IMODE(prepublication.mode) != 0o600
                    or prepublication.user_id != os.geteuid()
                ):
                    raise ProblemSolvingError(
                        "proposal temporary metadata is unsafe: "
                        f"{temporary.name}"
                    )
                try:
                    os.link(
                        temporary.name,
                        path.name,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as error:
                    raise ProblemSolvingError(
                        f"proposal archive collision: {path.name}"
                    ) from error
                published = True
                linked = self._require_regular_links(
                    os.fstat(descriptor),
                    name=path.name,
                    expected_links=2,
                )
                temporary_link = self._require_regular_links(
                    self._stat_named(directory_fd, temporary.name),
                    name=temporary.name,
                    expected_links=2,
                )
                published_link = self._require_regular_links(
                    self._stat_named(directory_fd, path.name),
                    name=path.name,
                    expected_links=2,
                )
                if (
                    linked != temporary_link
                    or linked != published_link
                    or linked.publication_fields()
                    != prepublication.publication_fields()
                ):
                    raise ProblemSolvingError(
                        "proposal archive publication changed before cleanup: "
                        f"{path.name}"
                    )

                if not self._unlink_if_same_file(
                    directory_fd,
                    temporary.name,
                    descriptor,
                ):
                    raise ProblemSolvingError(
                        "proposal temporary changed during cleanup: "
                        f"{temporary.name}"
                    )
                temporary_created = False
                post_unlink_descriptor = os.fstat(descriptor)
                post_unlink_name = self._stat_named(
                    directory_fd,
                    path.name,
                )
                if (
                    post_unlink_descriptor.st_nlink != 1
                    or post_unlink_name.st_nlink != 1
                ):
                    raise ProblemSolvingError(
                        "proposal archive publication has an unsafe "
                        f"hard link: {path.name}"
                    )
                postpublication = self._verify_named_payload(
                    directory_fd,
                    path.name,
                    descriptor,
                    payload=payload,
                    expected_digest=expected_digest,
                )
                if (
                    postpublication.publication_fields()
                    != prepublication.publication_fields()
                ):
                    raise ProblemSolvingError(
                        "proposal archive publication metadata changed: "
                        f"{path.name}"
                    )
                os.fsync(directory_fd)
                final_descriptor = self._require_regular_links(
                    os.fstat(descriptor),
                    name=path.name,
                    expected_links=1,
                )
                final_name = self._require_regular_links(
                    self._stat_named(directory_fd, path.name),
                    name=path.name,
                    expected_links=1,
                )
                if (
                    final_descriptor != postpublication
                    or final_name != postpublication
                ):
                    raise ProblemSolvingError(
                        "proposal archive publication changed after cleanup: "
                        f"{path.name}"
                    )
                publication_verified = True
            finally:
                if published and not publication_verified:
                    self._unlink_if_same_file(
                        directory_fd,
                        path.name,
                        descriptor,
                    )
                if temporary_created:
                    self._unlink_if_same_file(
                        directory_fd,
                        temporary.name,
                        descriptor,
                    )
                os.close(descriptor)
        finally:
            os.close(directory_fd)

    def _open_directory(self) -> int:
        try:
            current = self._root.resolve(strict=True)
            current_stat = current.stat()
        except (FileNotFoundError, OSError) as error:
            raise ProblemSolvingError(
                "proposal archive directory changed or is unavailable"
            ) from error
        if (
            current != self._root
            or (
                current_stat.st_dev,
                current_stat.st_ino,
            )
            != self._root_identity
        ):
            raise ProblemSolvingError("proposal archive directory changed")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._root, flags)
        except OSError as error:
            raise ProblemSolvingError(
                "proposal archive directory changed or is unavailable"
            ) from error
        opened_stat = os.fstat(descriptor)
        if (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ) != self._root_identity or opened_stat.st_mode & 0o077:
            os.close(descriptor)
            raise ProblemSolvingError("proposal archive directory changed")
        return descriptor

    def _validate_child(self, path: Path) -> None:
        """Resolve and enforce direct-child containment at operation time."""
        try:
            parent = path.parent.resolve(strict=True)
            resolved = path.resolve(strict=False)
        except (FileNotFoundError, OSError) as error:
            raise ProblemSolvingError(
                "proposal archive child cannot be resolved"
            ) from error
        if parent != self._root or resolved.parent != self._root:
            raise ProblemSolvingError(
                f"proposal archive child escapes archive: {path.name}"
            )
        if path.is_symlink():
            raise ProblemSolvingError(
                f"proposal archive symlink is forbidden: {path.name}"
            )

    @staticmethod
    def _entry_exists(directory_fd: int, name: str) -> bool:
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def _read_existing(
        self,
        directory_fd: int,
        name: str,
        *,
        maximum_bytes: int,
    ) -> bytes | None:
        try:
            named_before = self._require_regular_links(
                self._stat_named(directory_fd, name),
                name=name,
                expected_links=1,
            )
        except FileNotFoundError:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
        except OSError as error:
            raise ProblemSolvingError(
                f"proposal archive child cannot be opened: {name}"
            ) from error
        try:
            descriptor_before = self._require_regular_links(
                os.fstat(descriptor),
                name=name,
                expected_links=1,
            )
            if descriptor_before != named_before:
                raise ProblemSolvingError(
                    f"proposal archive child changed during read: {name}"
                )
            retained = self._read_bounded(
                descriptor,
                maximum_bytes=maximum_bytes,
                label=f"proposal archive child {name}",
            )
            descriptor_after = self._require_regular_links(
                os.fstat(descriptor),
                name=name,
                expected_links=1,
            )
            try:
                named_after = self._require_regular_links(
                    self._stat_named(directory_fd, name),
                    name=name,
                    expected_links=1,
                )
            except FileNotFoundError as error:
                raise ProblemSolvingError(
                    f"proposal archive child changed during read: {name}"
                ) from error
            if not (
                descriptor_after
                == descriptor_before
                == named_before
                == named_after
            ):
                raise ProblemSolvingError(
                    f"proposal archive child changed during read: {name}"
                )
            return retained
        finally:
            os.close(descriptor)

    def _verify_named_payload(
        self,
        directory_fd: int,
        name: str,
        descriptor: int,
        *,
        payload: bytes,
        expected_digest: bytes,
    ) -> _FileMetadata:
        descriptor_before = self._require_regular_links(
            os.fstat(descriptor),
            name=name,
            expected_links=1,
        )
        try:
            named_before = self._require_regular_links(
                self._stat_named(directory_fd, name),
                name=name,
                expected_links=1,
            )
        except FileNotFoundError as error:
            raise ProblemSolvingError(
                f"proposal archive publication changed: {name}"
            ) from error
        if descriptor_before != named_before:
            raise ProblemSolvingError(
                f"proposal archive publication changed: {name}"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        retained = self._read_bounded(
            descriptor,
            maximum_bytes=len(payload),
            label=f"proposal archive publication {name}",
        )
        descriptor_after = self._require_regular_links(
            os.fstat(descriptor),
            name=name,
            expected_links=1,
        )
        try:
            named_after = self._require_regular_links(
                self._stat_named(directory_fd, name),
                name=name,
                expected_links=1,
            )
        except FileNotFoundError as error:
            raise ProblemSolvingError(
                f"proposal archive publication changed: {name}"
            ) from error
        if not (
            descriptor_after == descriptor_before == named_before == named_after
        ):
            raise ProblemSolvingError(
                f"proposal archive publication changed: {name}"
            )
        retained_digest = hashlib.sha256(retained).digest()
        if retained != payload or not hmac.compare_digest(
            retained_digest,
            expected_digest,
        ):
            raise ProblemSolvingError(
                f"proposal archive publication payload changed: {name}"
            )
        return descriptor_after

    @staticmethod
    def _stat_named(directory_fd: int, name: str) -> os.stat_result:
        return os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )

    @staticmethod
    def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)

    @staticmethod
    def _require_regular_links(
        value: os.stat_result,
        *,
        name: str,
        expected_links: int,
    ) -> _FileMetadata:
        if not stat.S_ISREG(value.st_mode):
            if stat.S_ISLNK(value.st_mode):
                raise ProblemSolvingError(
                    f"proposal archive symlink is forbidden: {name}"
                )
            raise ProblemSolvingError(
                f"proposal archive child is not a regular file: {name}"
            )
        if value.st_nlink != expected_links:
            if expected_links == 1:
                raise ProblemSolvingError(
                    f"proposal archive hard links are forbidden: {name}"
                )
            raise ProblemSolvingError(
                "proposal archive publication acquired an unexpected "
                f"hard link: {name}"
            )
        return _FileMetadata.capture(value)

    @classmethod
    def _unlink_if_same_file(
        cls,
        directory_fd: int,
        name: str,
        descriptor: int,
    ) -> bool:
        try:
            descriptor_stat = os.fstat(descriptor)
            named_stat = cls._stat_named(directory_fd, name)
            if not cls._same_file(descriptor_stat, named_stat):
                return False
            os.unlink(name, dir_fd=directory_fd)
        except OSError:
            return False
        return True

    @staticmethod
    def _read_bounded(
        descriptor: int,
        *,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        retained = bytearray()
        while len(retained) <= maximum_bytes:
            remaining = maximum_bytes - len(retained)
            chunk = os.read(
                descriptor,
                min(_READ_CHUNK_BYTES, remaining + 1),
            )
            if not chunk:
                return bytes(retained)
            if len(chunk) > remaining:
                raise ProblemSolvingError(f"{label} exceeds its byte limit")
            retained.extend(chunk)
        raise ProblemSolvingError(f"{label} exceeds its byte limit")
