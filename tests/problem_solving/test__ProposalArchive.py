from __future__ import annotations

import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving.archive import ProposalArchive
from projectkoios.courses.problem_solving.models import ProblemSolvingError


def archive_at(path: Path) -> ProposalArchive:
    path.mkdir(mode=0o700)
    return ProposalArchive(path)


def write_complete(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def assert_incomplete_bytes(path: Path, expected: bytes) -> None:
    assert stat.S_IMODE(path.stat().st_mode) == 0o000
    path.chmod(0o600)
    assert path.read_bytes() == expected


def test__initialization__fails_closed_without_directory_locking(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.fcntl.flock",
            side_effect=OSError("unsupported"),
        ),
        pytest.raises(ProblemSolvingError, match="locking is unavailable"),
    ):
        ProposalArchive(root)


def test__write_or_confirm__serializes_same_payload_writers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    first_archive = archive_at(root)
    second_archive = ProposalArchive(root)
    child = first_archive.child("request.json")
    barrier = threading.Barrier(2)

    def write(archive: ProposalArchive) -> None:
        barrier.wait()
        archive.write_or_confirm(child, b"payload")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write, first_archive),
            executor.submit(write, second_archive),
        ]
        for future in futures:
            future.result(timeout=5)

    assert child.read_bytes() == b"payload"
    assert not tuple(root.glob("*.tmp"))


def test__write_or_confirm__serializes_different_payload_writers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    first_archive = archive_at(root)
    second_archive = ProposalArchive(root)
    child = first_archive.child("request.json")
    barrier = threading.Barrier(2)

    def write(archive: ProposalArchive, payload: bytes) -> str | None:
        barrier.wait()
        try:
            archive.write_or_confirm(child, payload)
        except ProblemSolvingError as error:
            return str(error)
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write, first_archive, b"first"),
            executor.submit(write, second_archive, b"other"),
        ]
        outcomes = [future.result(timeout=5) for future in futures]

    assert outcomes.count(None) == 1
    assert sum("archive collision" in value for value in outcomes if value) == 1
    assert child.read_bytes() in {b"first", b"other"}


def test__read_optional__serializes_cooperative_readers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    write_complete(child, b"payload")
    first_entered = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    calls = 0
    real_read_bounded = ProposalArchive._read_bounded

    def blocking_read(
        descriptor: int,
        *,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        else:
            second_entered.set()
        return real_read_bounded(
            descriptor,
            maximum_bytes=maximum_bytes,
            label=label,
        )

    def second_read() -> bytes | None:
        second_started.set()
        return archive.read_optional(child, maximum_bytes=64)

    with (
        patch.object(
            ProposalArchive,
            "_read_bounded",
            side_effect=blocking_read,
        ),
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(archive.read_optional, child, maximum_bytes=64)
        assert first_entered.wait(timeout=5)
        second = executor.submit(second_read)
        assert second_started.wait(timeout=5)
        assert not second_entered.wait(timeout=0.2)
        release_first.set()
        assert first.result(timeout=5) == b"payload"
        assert second.result(timeout=5) == b"payload"
        assert second_entered.is_set()


def test__write_or_confirm__short_write_failure_retains_partial_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    real_write = os.write
    writes = 0

    def failing_write(descriptor: int, payload: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(descriptor, payload[:3])
        raise OSError("injected write failure")

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.write",
            side_effect=failing_write,
        ),
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink"
        ) as unlink,
        pytest.raises(ProblemSolvingError, match="incomplete record retained"),
    ):
        archive.write_or_confirm(child, b"payload")

    unlink.assert_not_called()
    assert_incomplete_bytes(child, b"pay")


@pytest.mark.parametrize("failure_call", (1, 2, 3))
def test__write_or_confirm__fsync_failure_retains_incomplete_record(
    tmp_path: Path,
    failure_call: int,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    real_fsync = os.fsync
    calls = 0

    def failing_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected fsync failure")
        real_fsync(descriptor)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.fsync",
            side_effect=failing_fsync,
        ),
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink"
        ) as unlink,
        pytest.raises(ProblemSolvingError, match="incomplete record retained"),
    ):
        archive.write_or_confirm(child, b"payload")

    unlink.assert_not_called()
    assert stat.S_IMODE(child.stat().st_mode) == 0o000
    with pytest.raises(ProblemSolvingError, match="operator reconciliation"):
        archive.write_or_confirm(child, b"payload")
    assert_incomplete_bytes(child, b"payload")


def test__write_or_confirm__verification_failure_retains_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    reads = iter((b"corrupt", b""))

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=lambda _descriptor, _maximum: next(reads),
        ),
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink"
        ) as unlink,
        pytest.raises(ProblemSolvingError, match="payload changed"),
    ):
        archive.write_or_confirm(child, b"payload")

    unlink.assert_not_called()
    assert_incomplete_bytes(child, b"payload")


def test__write_or_confirm__retry_detects_partial_fail_stop_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    real_write = os.write
    writes = 0

    def failing_write(descriptor: int, payload: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(descriptor, payload[:3])
        raise OSError("injected write failure")

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.write",
            side_effect=failing_write,
        ),
        pytest.raises(ProblemSolvingError, match="incomplete record retained"),
    ):
        archive.write_or_confirm(child, b"payload")

    with pytest.raises(ProblemSolvingError, match="operator reconciliation"):
        archive.write_or_confirm(child, b"payload")

    assert_incomplete_bytes(child, b"pay")


def test__write_or_confirm__never_unlinks_noncooperative_replacement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    replacement = root / "replacement"
    replacement.write_bytes(b"replacement")
    real_read = os.read
    replaced = False

    def replacing_read(descriptor: int, maximum_bytes: int) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement, child)
        return real_read(descriptor, maximum_bytes)

    with (
        patch(
            "projectkoios.courses.problem_solving.archive.os.read",
            side_effect=replacing_read,
        ),
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink",
            side_effect=AssertionError("library must not unlink archive names"),
        ) as unlink,
        pytest.raises(ProblemSolvingError, match="changed"),
    ):
        archive.write_or_confirm(child, b"payload")

    unlink.assert_not_called()
    assert replaced
    assert child.read_bytes() == b"replacement"


def test__read_optional__rejects_preexisting_outside_hardlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    write_complete(child, b"payload")
    os.link(child, tmp_path / "outside.json")

    with pytest.raises(ProblemSolvingError, match="hard links are forbidden"):
        archive.read_optional(child, maximum_bytes=64)


def test__read_optional__rejects_hardlink_added_after_nofollow_open(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    write_complete(child, b"payload")
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


def test__read_optional__rejects_in_place_mutation_during_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("response.json")
    write_complete(child, b"payload")
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


def test__write_or_confirm__rejects_mutated_existing_child(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    write_complete(child, b"payload")
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


def test__write_or_confirm__rejects_hardlink_added_during_verification(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
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
        patch(
            "projectkoios.courses.problem_solving.archive.os.unlink"
        ) as unlink,
        pytest.raises(ProblemSolvingError, match="hard links are forbidden"),
    ):
        archive.write_or_confirm(child, b"payload")

    unlink.assert_not_called()
    assert_incomplete_bytes(child, b"payload")
    assert outside.read_bytes() == b"payload"


def test__write_or_confirm__preserves_existing_collision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    archive = archive_at(root)
    child = archive.child("request.json")
    write_complete(child, b"differe")

    with pytest.raises(ProblemSolvingError, match="archive collision"):
        archive.write_or_confirm(child, b"payload")

    assert child.read_bytes() == b"differe"
