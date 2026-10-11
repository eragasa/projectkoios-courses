"""Cooperatively locked, bounded archive operations for proposal adapters."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
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


class ProposalArchive:
    """Operate on direct children under one cooperative directory lock."""

    def __init__(self, directory: Path) -> None:
        flags = self._directory_open_flags()
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
            self._lock_directory(descriptor)
            self._unlock_directory(descriptor)
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
        with self._locked_directory() as directory_fd:
            self._validate_child(path)
            return self._read_existing(
                directory_fd,
                path.name,
                maximum_bytes=maximum_bytes,
            )

    def write_or_confirm(self, path: Path, payload: bytes) -> None:
        with self._locked_directory() as directory_fd:
            self._validate_child(path)
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

            # The lock serializes cooperating users. O_EXCL preserves a
            # collision raised by a process that does not honor the lock.
            self._validate_child(path)
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | self._nofollow_flag()
            try:
                descriptor = os.open(
                    path.name,
                    flags,
                    0o000,
                    dir_fd=directory_fd,
                )
            except FileExistsError as error:
                raise ProblemSolvingError(
                    f"proposal archive collision: {path.name}"
                ) from error
            except OSError as error:
                raise ProblemSolvingError(
                    f"proposal archive child cannot be created: {path.name}"
                ) from error

            # Once O_EXCL creates the final name, every failure is fail-stop.
            # Never unlink that name: a noncooperating same-UID process could
            # replace it between any identity check and pathname deletion.
            try:
                try:
                    self._write_all(descriptor, payload, name=path.name)
                    os.fsync(descriptor)
                    expected_digest = hashlib.sha256(payload).digest()
                    staged = self._verify_named_payload(
                        directory_fd,
                        path.name,
                        descriptor,
                        payload=payload,
                        expected_digest=expected_digest,
                    )
                    if (
                        stat.S_IMODE(staged.mode) != 0o000
                        or staged.user_id != os.geteuid()
                    ):
                        raise ProblemSolvingError(
                            "proposal archive publication metadata is unsafe: "
                            f"{path.name}"
                        )
                    os.fsync(directory_fd)

                    # Mode 000 is the persistent incomplete-state marker. Set
                    # the readable mode only after the staged record is synced,
                    # then verify and sync that completed state as well.
                    os.fchmod(descriptor, 0o600)
                    completed = self._verify_named_payload(
                        directory_fd,
                        path.name,
                        descriptor,
                        payload=payload,
                        expected_digest=expected_digest,
                    )
                    self._require_complete_metadata(
                        completed,
                        name=path.name,
                    )
                    os.fsync(descriptor)
                    final_descriptor = self._require_regular_single_link(
                        os.fstat(descriptor),
                        name=path.name,
                    )
                    final_name = self._require_regular_single_link(
                        self._stat_named(directory_fd, path.name),
                        name=path.name,
                    )
                    if not (final_descriptor == completed == final_name):
                        raise ProblemSolvingError(
                            "proposal archive publication changed after "
                            f"verification: {path.name}"
                        )
                except OSError as error:
                    raise ProblemSolvingError(
                        "proposal archive publication failed; incomplete "
                        f"record retained: {path.name}"
                    ) from error
            except BaseException:
                self._mark_incomplete(descriptor)
                raise
            finally:
                os.close(descriptor)

    @contextmanager
    def _locked_directory(self) -> Iterator[int]:
        directory_fd = self._open_directory()
        locked = False
        try:
            self._lock_directory(directory_fd)
            locked = True
            yield directory_fd
        finally:
            try:
                if locked:
                    self._unlock_directory(directory_fd)
            finally:
                os.close(directory_fd)

    @staticmethod
    def _lock_directory(directory_fd: int) -> None:
        try:
            flock = fcntl.flock
            operation = fcntl.LOCK_EX
        except AttributeError as error:
            raise ProblemSolvingError(
                "proposal archive POSIX advisory locking is unavailable"
            ) from error
        while True:
            try:
                flock(directory_fd, operation)
                return
            except InterruptedError:
                continue
            except OSError as error:
                raise ProblemSolvingError(
                    "proposal archive POSIX advisory locking is unavailable"
                ) from error

    @staticmethod
    def _unlock_directory(directory_fd: int) -> None:
        try:
            flock = fcntl.flock
            operation = fcntl.LOCK_UN
        except AttributeError as error:
            raise ProblemSolvingError(
                "proposal archive POSIX advisory unlocking is unavailable"
            ) from error
        while True:
            try:
                flock(directory_fd, operation)
                return
            except InterruptedError:
                continue
            except OSError as error:
                raise ProblemSolvingError(
                    "proposal archive POSIX advisory unlocking is unavailable"
                ) from error

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
        try:
            descriptor = os.open(self._root, self._directory_open_flags())
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

    def _read_existing(
        self,
        directory_fd: int,
        name: str,
        *,
        maximum_bytes: int,
    ) -> bytes | None:
        try:
            named_before = self._require_regular_single_link(
                self._stat_named(directory_fd, name),
                name=name,
            )
        except FileNotFoundError:
            return None
        self._require_complete_metadata(named_before, name=name)
        flags = os.O_RDONLY | self._nofollow_flag()
        try:
            descriptor = os.open(name, flags, dir_fd=directory_fd)
        except OSError as error:
            raise ProblemSolvingError(
                f"proposal archive child cannot be opened: {name}"
            ) from error
        try:
            descriptor_before = self._require_regular_single_link(
                os.fstat(descriptor),
                name=name,
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
            descriptor_after = self._require_regular_single_link(
                os.fstat(descriptor),
                name=name,
            )
            try:
                named_after = self._require_regular_single_link(
                    self._stat_named(directory_fd, name),
                    name=name,
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
        descriptor_before = self._require_regular_single_link(
            os.fstat(descriptor),
            name=name,
        )
        try:
            named_before = self._require_regular_single_link(
                self._stat_named(directory_fd, name),
                name=name,
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
        descriptor_after = self._require_regular_single_link(
            os.fstat(descriptor),
            name=name,
        )
        try:
            named_after = self._require_regular_single_link(
                self._stat_named(directory_fd, name),
                name=name,
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
    def _require_complete_metadata(
        value: _FileMetadata,
        *,
        name: str,
    ) -> None:
        if stat.S_IMODE(value.mode) != 0o600 or value.user_id != os.geteuid():
            raise ProblemSolvingError(
                "proposal archive record is incomplete or unsafe and requires "
                f"operator reconciliation: {name}"
            )

    @staticmethod
    def _mark_incomplete(descriptor: int) -> None:
        try:
            os.fchmod(descriptor, 0o000)
        except OSError:
            # The descriptor may already refer to an unlinked inode. Never
            # fall back to a pathname operation that could affect a replacement.
            pass

    @staticmethod
    def _write_all(descriptor: int, payload: bytes, *, name: str) -> None:
        view = memoryview(payload)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count < 1 or count > len(view) - written:
                raise ProblemSolvingError(
                    "proposal archive write made invalid progress; incomplete "
                    f"record retained: {name}"
                )
            written += count

    @staticmethod
    def _stat_named(directory_fd: int, name: str) -> os.stat_result:
        return os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )

    @staticmethod
    def _require_regular_single_link(
        value: os.stat_result,
        *,
        name: str,
    ) -> _FileMetadata:
        if not stat.S_ISREG(value.st_mode):
            if stat.S_ISLNK(value.st_mode):
                raise ProblemSolvingError(
                    f"proposal archive symlink is forbidden: {name}"
                )
            raise ProblemSolvingError(
                f"proposal archive child is not a regular file: {name}"
            )
        if value.st_nlink < 1:
            raise ProblemSolvingError(f"proposal archive child changed: {name}")
        if value.st_nlink > 1:
            raise ProblemSolvingError(
                f"proposal archive hard links are forbidden: {name}"
            )
        return _FileMetadata.capture(value)

    @staticmethod
    def _nofollow_flag() -> int:
        try:
            return os.O_NOFOLLOW
        except AttributeError as error:
            raise ProblemSolvingError(
                "proposal archive no-follow opens are unavailable"
            ) from error

    @classmethod
    def _directory_open_flags(cls) -> int:
        try:
            directory = os.O_DIRECTORY
        except AttributeError as error:
            raise ProblemSolvingError(
                "proposal archive descriptor-relative operations are "
                "unavailable"
            ) from error
        return os.O_RDONLY | directory | cls._nofollow_flag()

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
