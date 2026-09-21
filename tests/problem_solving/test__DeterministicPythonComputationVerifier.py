from __future__ import annotations

import sys
from pathlib import Path

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


def verifier() -> DeterministicPythonComputationVerifier:
    return DeterministicPythonComputationVerifier(
        PythonVerifierConfiguration(
            python_executable=Path(sys.executable),
            runtime_identity=RUNTIME,
            dependency_lock_identity=LOCK,
            sandbox_command_prefix=("/usr/bin/env",),
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
