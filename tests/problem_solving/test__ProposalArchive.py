from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving.archive import ProposalArchive
from projectkoios.courses.problem_solving.models import ProblemSolvingError


def archive_at(path: Path) -> ProposalArchive:
    path.mkdir(mode=0o700)
    return ProposalArchive(path)


def test__read_optional__rejects_preexisting_outside_hardlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    child.write_bytes(b"payload")
    os.link(child, tmp_path / "outside.json")

    with pytest.raises(ProblemSolvingError, match="hard links are forbidden"):
        archive.read_optional(child, maximum_bytes=64)


def test__read_optional__rejects_hardlink_added_after_nofollow_open(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    child.write_bytes(b"payload")
    outside = tmp_path / "outside.json"
    real_open = os.open

    def racing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == child.name:
            os.link(child, outside)
        return descriptor

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.open",
            side_effect=racing_open,
        ),
        pytest.raises(ProblemSolvingError, match="hard links are forbidden"),
    ):
        archive.read_optional(child, maximum_bytes=64)


def test__read_optional__rejects_outside_hardlink_on_first_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    child.write_bytes(b"payload")
    outside = tmp_path / "outside.json"
    real_read = os.read
    first_read = True

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            os.link(child, outside)
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=racing_read,
        ),
        pytest.raises(ProblemSolvingError, match="hard links are forbidden"),
    ):
        archive.read_optional(child, maximum_bytes=64)

    assert outside.read_bytes() == b"payload"


def test__read_optional__rejects_in_place_mutation_on_first_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    child.write_bytes(b"payload")
    real_read = os.read
    first_read = True

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            child.write_bytes(b"mutated")
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=racing_read,
        ),
        pytest.raises(ProblemSolvingError, match="changed during read"),
    ):
        archive.read_optional(child, maximum_bytes=64)


def test__write_or_confirm__rejects_hardlinked_existing_child(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    child.write_bytes(b"payload")
    os.link(child, tmp_path / "outside.json")

    with pytest.raises(ProblemSolvingError, match="hard links are forbidden"):
        archive.write_or_confirm(child, b"payload")


def test__write_or_confirm__rejects_outside_hardlink_on_first_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    child.write_bytes(b"payload")
    outside = tmp_path / "outside.json"
    real_read = os.read
    first_read = True

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            os.link(child, outside)
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=racing_read,
        ),
        pytest.raises(ProblemSolvingError, match="hard links are forbidden"),
    ):
        archive.write_or_confirm(child, b"payload")

    assert outside.read_bytes() == b"payload"


def test__write_or_confirm__rejects_in_place_mutation_on_first_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    child.write_bytes(b"payload")
    real_read = os.read
    first_read = True

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal first_read
        if first_read:
            first_read = False
            child.write_bytes(b"mutated")
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=racing_read,
        ),
        pytest.raises(ProblemSolvingError, match="changed during read"),
    ):
        archive.write_or_confirm(child, b"payload")


def test__write_or_confirm__preserves_destination_collision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    child.write_bytes(b"differe")

    with pytest.raises(ProblemSolvingError, match="archive collision"):
        archive.write_or_confirm(child, b"payload")

    assert child.read_bytes() == b"differe"
    assert not (root / ".request.json.tmp").exists()


def test__write_or_confirm__rejects_temp_mutation_after_fsync(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    temporary = root / ".request.json.tmp"
    real_fsync = os.fsync
    first_fsync = True

    def racing_fsync(descriptor: int) -> None:
        nonlocal first_fsync
        real_fsync(descriptor)
        if first_fsync:
            first_fsync = False
            temporary.write_bytes(b"mutated")

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.fsync",
            side_effect=racing_fsync,
        ),
        pytest.raises(ProblemSolvingError, match="payload changed"),
    ):
        archive.write_or_confirm(child, b"payload")

    assert not child.exists()
    assert not temporary.exists()


def test__write_or_confirm__removes_final_mutated_during_final_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    temporary = root / ".request.json.tmp"
    real_read = os.read
    mutated = False

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal mutated
        if child.exists() and not temporary.exists() and not mutated:
            mutated = True
            child.write_bytes(b"mutated")
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=racing_read,
        ),
        pytest.raises(ProblemSolvingError, match="publication changed"),
    ):
        archive.write_or_confirm(child, b"payload")

    assert mutated
    assert not child.exists()
    assert not temporary.exists()


def test__write_or_confirm__rejects_hardlink_added_during_publication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    outside = tmp_path / "outside.json"
    real_link = os.link

    def racing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        real_link(
            source,
            outside,
            src_dir_fd=src_dir_fd,
            follow_symlinks=False,
        )
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.link",
            side_effect=racing_link,
        ),
        pytest.raises(ProblemSolvingError, match="unexpected hard link"),
    ):
        archive.write_or_confirm(child, b"payload")

    assert not child.exists()
    assert outside.read_bytes() == b"payload"
    assert not (root / ".request.json.tmp").exists()


def test__write_or_confirm__requires_one_link_after_temporary_unlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    outside = tmp_path / "outside.json"
    real_unlink = os.unlink

    def racing_unlink(
        path: str,
        *,
        dir_fd: int | None = None,
    ) -> None:
        real_unlink(path, dir_fd=dir_fd)
        if path == ".request.json.tmp":
            os.link(child, outside)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink",
            side_effect=racing_unlink,
        ),
        pytest.raises(ProblemSolvingError, match="unsafe hard link"),
    ):
        archive.write_or_confirm(child, b"payload")

    assert not child.exists()
    assert outside.read_bytes() == b"payload"
