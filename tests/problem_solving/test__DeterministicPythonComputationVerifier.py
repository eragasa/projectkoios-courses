from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving import (
    ComputationPlan,
    DeterministicPythonComputationVerifier,
    ProblemSolvingError,
    PythonVerifierConfiguration,
    VerificationOutcome,
)

RUNTIME = "python:test-runtime"
LOCK = "lock:sha256:test-lock"


def verifier(
    *,
    maximum_output_bytes: int = 65_536,
    sandbox_command_prefix: tuple[str, ...] = ("/usr/bin/env",),
) -> DeterministicPythonComputationVerifier:
    return DeterministicPythonComputationVerifier(
        PythonVerifierConfiguration(
            python_executable=Path(sys.executable),
            runtime_identity=RUNTIME,
            dependency_lock_identity=LOCK,
            sandbox_command_prefix=sandbox_command_prefix,
            maximum_output_bytes=maximum_output_bytes,
        )
    )


def plan(source: str) -> ComputationPlan:
    return ComputationPlan(
        language="python",
        source_code=source,
        runtime_identity=RUNTIME,
        dependency_lock_identity=LOCK,
        seed=20260921,
        timeout_seconds=5,
        network_access=False,
    )


def test__verify__accepts_bounded_json_result() -> None:
    result = verifier().verify(
        plan(
            "import json\n"
            "value = 6 * 7\n"
            "print(json.dumps({"
            '"analytical_agreement": value == 42, '
            '"findings": [], '
            '"result_summary": "verified"}, sort_keys=True))\n'
        )
    )

    assert result.outcome is VerificationOutcome.PASSED
    assert result.analytical_agreement is True
    assert result.result_summary == "verified"
    assert len(result.output_sha256) == 64


def test__verify__rejects_network_module_import() -> None:
    with pytest.raises(ProblemSolvingError, match="import is forbidden"):
        verifier().verify(plan("import socket\nprint('not reached')\n"))


def test__verify__rejects_file_access_builtin() -> None:
    with pytest.raises(ProblemSolvingError, match="name is forbidden"):
        verifier().verify(plan("print(open('/etc/passwd').read())\n"))


def test__verify__rejects_dynamic_attribute_introspection() -> None:
    with pytest.raises(ProblemSolvingError, match="attribute is forbidden"):
        verifier().verify(plan("print((1).__class__)\n"))


def test__verify__rejects_unpinned_runtime() -> None:
    invalid = ComputationPlan(
        language="python",
        source_code="print('unused')\n",
        runtime_identity="python:other",
        dependency_lock_identity=LOCK,
        seed=0,
        timeout_seconds=1,
        network_access=False,
    )

    with pytest.raises(ProblemSolvingError, match="runtime identity"):
        verifier().verify(invalid)


def test__verify__reports_process_failure_without_acceptance() -> None:
    result = verifier().verify(plan("assert False\n"))

    assert result.outcome is VerificationOutcome.FAILED
    assert result.analytical_agreement is None


def test__verify__reports_invalid_output_contract_as_inconclusive() -> None:
    result = verifier().verify(plan("print('not-json')\n"))

    assert result.outcome is VerificationOutcome.INCONCLUSIVE
    assert result.analytical_agreement is None


def test__verify__terminates_at_streaming_output_bound() -> None:
    retained_lengths: list[int] = []

    def digest(stdout: bytes, stderr: bytes) -> str:
        retained_lengths.append(len(stdout) + len(stderr))
        value = hashlib.sha256()
        value.update(stdout)
        value.update(b"\0")
        value.update(stderr)
        return value.hexdigest()

    with patch.object(
        DeterministicPythonComputationVerifier,
        "_output_sha256",
        side_effect=digest,
    ):
        result = verifier(maximum_output_bytes=64).verify(
            plan("print('x' * 10000)\n")
        )

    assert result.outcome is VerificationOutcome.FAILED
    assert result.findings == ("output byte bound exceeded",)
    assert retained_lengths == [64]


def test__verify__terminates_process_group_on_timeout(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "wrapper.pid"
    wrapper = tmp_path / "sleeping_sandbox.py"
    wrapper.write_text(
        "import os\n"
        "import time\n"
        f"open({str(pid_path)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    timed_plan = replace(plan("print('{}')\n"), timeout_seconds=1)

    started = time.monotonic()
    result = verifier(
        sandbox_command_prefix=(sys.executable, str(wrapper)),
    ).verify(timed_plan)
    elapsed = time.monotonic() - started

    assert result.outcome is VerificationOutcome.FAILED
    assert result.findings == ("subprocess timeout",)
    assert elapsed < 3
    process_id = int(pid_path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(process_id, 0)
