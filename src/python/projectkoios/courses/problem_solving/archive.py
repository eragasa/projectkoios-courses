"""Race-resistant, bounded archive operations for proposal adapters."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .models import ProblemSolvingError

_READ_CHUNK_BYTES = 8192
_MAXIMUM_CHILD_NAME_BYTES = 240


class ProposalArchive:
    """Operate only on direct children of one immutable archive directory."""

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
            try:
                child_stat = os.stat(
                    path.name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(child_stat.st_mode):
                raise ProblemSolvingError(
                    f"proposal archive child is not a regular file: {path.name}"
                )
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(path.name, flags, dir_fd=directory_fd)
            except OSError as error:
                raise ProblemSolvingError(
                    f"proposal archive child cannot be opened: {path.name}"
                ) from error
            try:
                opened_stat = os.fstat(descriptor)
                if (
                    opened_stat.st_dev != child_stat.st_dev
                    or opened_stat.st_ino != child_stat.st_ino
                ):
                    raise ProblemSolvingError(
                        "proposal archive child changed during read: "
                        f"{path.name}"
                    )
                return self._read_bounded(
                    descriptor,
                    maximum_bytes=maximum_bytes,
                    label=f"proposal archive child {path.name}",
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(directory_fd)

    def write_or_confirm(self, path: Path, payload: bytes) -> None:
        directory_fd = self._open_directory()
        temporary = path.with_name(f".{path.name}.tmp")
        temporary_created = False
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
                os.O_WRONLY
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
            try:
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                written = 0
                while written < len(view):
                    written += os.write(descriptor, view[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            # link(2) publishes without replacing a child created by a race.
            self._validate_child(path)
            self._validate_child(temporary)
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
            os.unlink(temporary.name, dir_fd=directory_fd)
            temporary_created = False
            os.fsync(directory_fd)
        finally:
            if temporary_created:
                try:
                    os.unlink(temporary.name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
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
            child_stat = os.stat(
                name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(child_stat.st_mode):
            if stat.S_ISLNK(child_stat.st_mode):
                raise ProblemSolvingError(
                    f"proposal archive symlink is forbidden: {name}"
                )
            raise ProblemSolvingError(
                f"proposal archive child is not a regular file: {name}"
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        try:
            opened_stat = os.fstat(descriptor)
            if (
                opened_stat.st_dev != child_stat.st_dev
                or opened_stat.st_ino != child_stat.st_ino
            ):
                raise ProblemSolvingError(
                    f"proposal archive child changed during read: {name}"
                )
            return self._read_bounded(
                descriptor,
                maximum_bytes=maximum_bytes,
                label=f"proposal archive child {name}",
            )
        finally:
            os.close(descriptor)

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
