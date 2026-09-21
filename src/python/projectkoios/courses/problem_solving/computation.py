from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .models import (
    ComputationPlan,
    ComputationResult,
    ProblemSolvingError,
    VerificationOutcome,
)

_ALLOWED_MODULES = frozenset({"json", "math", "sympy"})
_ALLOWED_BUILTIN_CALLS = frozenset(
    {"abs", "bool", "float", "int", "len", "print", "str"}
)
_FORBIDDEN_NAMES = frozenset(
    {
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
        "__import__",
    }
)
_FORBIDDEN_NODES = (
    ast.AsyncFor,
    ast.AsyncFunctionDef,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.Delete,
    ast.For,
    ast.FunctionDef,
    ast.Global,
    ast.Lambda,
    ast.Match,
    ast.NamedExpr,
    ast.Nonlocal,
    ast.Raise,
    ast.Try,
    ast.While,
    ast.With,
    ast.Yield,
    ast.YieldFrom,
)


@dataclass(frozen=True)
class PythonVerifierConfiguration:
    python_executable: Path
    runtime_identity: str
    dependency_lock_identity: str
    sandbox_command_prefix: tuple[str, ...]
    maximum_source_bytes: int = 16_384
    maximum_output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if not self.python_executable.is_absolute():
            raise ProblemSolvingError("Python executable must be absolute")
        if not self.python_executable.is_file():
            raise ProblemSolvingError("Python executable is unavailable")
        if not self.runtime_identity or not self.dependency_lock_identity:
            raise ProblemSolvingError(
                "verifier environment identities are required"
            )
        if not self.sandbox_command_prefix:
            raise ProblemSolvingError("an external sandbox command is required")
        if self.maximum_source_bytes < 1 or self.maximum_output_bytes < 1:
            raise ProblemSolvingError("verifier byte bounds must be positive")


class DeterministicPythonComputationVerifier:
    """Runs a restricted script under an externally configured sandbox."""

    def __init__(self, configuration: PythonVerifierConfiguration) -> None:
        self._configuration = configuration

    def verify(self, plan: ComputationPlan) -> ComputationResult:
        self._validate_plan(plan)
        source = plan.source_code.encode("utf-8")
        if len(source) > self._configuration.maximum_source_bytes:
            raise ProblemSolvingError("computation source exceeds its bound")
        self._validate_source(plan.source_code)

        with tempfile.TemporaryDirectory(
            prefix="projectkoios-computation-"
        ) as temporary_directory:
            directory = Path(temporary_directory)
            os.chmod(directory, 0o700)
            script_path = directory / "computation.py"
            script_path.write_bytes(source)
            os.chmod(script_path, 0o600)
            command = (
                *self._configuration.sandbox_command_prefix,
                str(self._configuration.python_executable),
                "-I",
                str(script_path),
            )
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONHASHSEED": str(plan.seed),
            }
            try:
                completed = subprocess.run(
                    command,
                    cwd=directory,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=plan.timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                output = (error.stdout or b"") + b"\0" + (
                    error.stderr or b""
                )
                return ComputationResult(
                    plan_identity=plan.identity,
                    outcome=VerificationOutcome.FAILED,
                    result_summary="computation exceeded its timeout",
                    output_sha256=hashlib.sha256(output).hexdigest(),
                    analytical_agreement=None,
                    findings=("subprocess timeout",),
                )

        combined = completed.stdout + b"\0" + completed.stderr
        output_sha256 = hashlib.sha256(combined).hexdigest()
        if len(completed.stdout) + len(completed.stderr) > (
            self._configuration.maximum_output_bytes
        ):
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.FAILED,
                result_summary="computation output exceeded its bound",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=("output byte bound exceeded",),
            )
        if completed.returncode != 0:
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.FAILED,
                result_summary="computation process failed",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=(
                    f"subprocess exit status {completed.returncode}",
                ),
            )
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
            summary = payload["result_summary"]
            agreement = payload["analytical_agreement"]
            findings = payload.get("findings", [])
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.INCONCLUSIVE,
                result_summary="computation output contract was invalid",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=("invalid JSON output contract",),
            )
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(agreement, bool)
            or not isinstance(findings, list)
            or not all(isinstance(item, str) for item in findings)
        ):
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.INCONCLUSIVE,
                result_summary="computation output contract was invalid",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=("invalid JSON output value types",),
            )
        return ComputationResult(
            plan_identity=plan.identity,
            outcome=VerificationOutcome.PASSED,
            result_summary=summary,
            output_sha256=output_sha256,
            analytical_agreement=agreement,
            findings=tuple(findings),
        )

    def _validate_plan(self, plan: ComputationPlan) -> None:
        if plan.runtime_identity != self._configuration.runtime_identity:
            raise ProblemSolvingError("computation runtime identity mismatch")
        if (
            plan.dependency_lock_identity
            != self._configuration.dependency_lock_identity
        ):
            raise ProblemSolvingError(
                "computation dependency lock identity mismatch"
            )
        if plan.network_access:
            raise ProblemSolvingError("networked computation is forbidden")

    @staticmethod
    def _validate_source(source: str) -> None:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as error:
            raise ProblemSolvingError(
                "computation source is not valid Python"
            ) from error
        imported_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, _FORBIDDEN_NODES):
                raise ProblemSolvingError(
                    f"computation syntax is forbidden: {type(node).__name__}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in _ALLOWED_MODULES:
                        raise ProblemSolvingError(
                            f"computation import is forbidden: {alias.name}"
                        )
                    imported_aliases.add(alias.asname or alias.name)
            if isinstance(node, ast.ImportFrom):
                raise ProblemSolvingError("from-imports are forbidden")
            if isinstance(node, ast.Name):
                if node.id.startswith("_") or node.id in _FORBIDDEN_NAMES:
                    raise ProblemSolvingError(
                        f"computation name is forbidden: {node.id}"
                    )
            if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
                raise ProblemSolvingError(
                    f"computation attribute is forbidden: {node.attr}"
                )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name):
                if function.id not in _ALLOWED_BUILTIN_CALLS:
                    raise ProblemSolvingError(
                        f"computation call is forbidden: {function.id}"
                    )
                continue
            if isinstance(function, ast.Attribute):
                if not isinstance(function.value, ast.Name):
                    raise ProblemSolvingError(
                        "nested computation attribute calls are forbidden"
                    )
                if function.value.id not in imported_aliases:
                    raise ProblemSolvingError(
                        "computation method calls are forbidden"
                    )
                continue
            raise ProblemSolvingError("dynamic computation calls are forbidden")
