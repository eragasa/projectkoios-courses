from __future__ import annotations

import ast
import hashlib
import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
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
_OUTPUT_READ_CHUNK_BYTES = 8192
_PROCESS_TERMINATION_GRACE_SECONDS = 0.2

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


@dataclass(frozen=True)
class _ProcessCapture:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    output_exceeded: bool


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
            process = subprocess.Popen(
                command,
                cwd=directory,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            capture = self._capture_process_output(
                process,
                timeout_seconds=plan.timeout_seconds,
            )

        output_sha256 = self._output_sha256(
            capture.stdout,
            capture.stderr,
        )
        if capture.output_exceeded:
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.FAILED,
                result_summary="computation output exceeded its bound",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=("output byte bound exceeded",),
            )
        if capture.timed_out:
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.FAILED,
                result_summary="computation exceeded its timeout",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=("subprocess timeout",),
            )
        if capture.returncode != 0:
            return ComputationResult(
                plan_identity=plan.identity,
                outcome=VerificationOutcome.FAILED,
                result_summary="computation process failed",
                output_sha256=output_sha256,
                analytical_agreement=None,
                findings=(f"subprocess exit status {capture.returncode}",),
            )
        try:
            payload = json.loads(capture.stdout.decode("utf-8"))
            summary = payload["result_summary"]
            agreement = payload["analytical_agreement"]
            findings = payload.get("findings", [])
        except KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError:
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

    def _capture_process_output(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: int,
    ) -> _ProcessCapture:
        if process.stdout is None or process.stderr is None:
            self._terminate_process(process)
            raise ProblemSolvingError(
                "computation output pipes are unavailable"
            )

        output = {"stdout": bytearray(), "stderr": bytearray()}
        selector = selectors.DefaultSelector()
        streams = (process.stdout, process.stderr)
        for name, stream in zip(output, streams, strict=True):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        output_exceeded = False
        stopped = False
        try:
            while selector.get_map():
                if not stopped and time.monotonic() >= deadline:
                    timed_out = True
                    stopped = True
                    self._terminate_process(process)
                wait_seconds = 0.05
                if not stopped:
                    wait_seconds = max(
                        0.0,
                        min(wait_seconds, deadline - time.monotonic()),
                    )
                for key, _ in selector.select(wait_seconds):
                    retained = sum(len(value) for value in output.values())
                    remaining = (
                        self._configuration.maximum_output_bytes - retained
                    )
                    chunk = os.read(
                        key.fd,
                        min(_OUTPUT_READ_CHUNK_BYTES, remaining + 1),
                    )
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = output[str(key.data)]
                    target.extend(chunk[:remaining])
                    if len(chunk) > remaining and not output_exceeded:
                        output_exceeded = True
                        stopped = True
                        self._terminate_process(process)
            if process.poll() is None:
                process.wait()
        finally:
            selector.close()
            for stream in streams:
                stream.close()
            if process.poll() is None:
                self._terminate_process(process)

        return _ProcessCapture(
            returncode=process.returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            timed_out=timed_out,
            output_exceeded=output_exceeded,
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    @staticmethod
    def _output_sha256(stdout: bytes, stderr: bytes) -> str:
        digest = hashlib.sha256()
        digest.update(stdout)
        digest.update(b"\0")
        digest.update(stderr)
        return digest.hexdigest()

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
