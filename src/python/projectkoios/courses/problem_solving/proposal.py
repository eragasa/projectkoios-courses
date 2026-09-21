from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import (
    AnalyticalWork,
    ComputationPlan,
    EvidencePassage,
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    SolutionDraft,
)

_NUMERIC_LITERAL_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[eE][-+]?\d+)?(?![\w.])"
)


@dataclass(frozen=True)
class OllamaProposalConfiguration:
    endpoint: str
    model: str
    model_digest: str
    minimum_runtime_version: tuple[int, int, int]
    system_prompt: str
    system_prompt_sha256: str
    temperature: float
    seed: int
    top_k: int
    top_p: float
    context_tokens: int
    prediction_tokens: int
    timeout_seconds: int
    computation_runtime_identity: str
    computation_dependency_lock_identity: str
    computation_timeout_seconds: int
    archive_directory: Path
    configured_computation_sources: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        prompt_digest = hashlib.sha256(
            self.system_prompt.encode("utf-8")
        ).hexdigest()
        if prompt_digest != self.system_prompt_sha256:
            raise ProblemSolvingError("system prompt identity mismatch")
        if not self.endpoint.startswith("http://127.0.0.1:"):
            raise ProblemSolvingError("Ollama endpoint must be local")
        if len(self.model_digest) != 64:
            raise ProblemSolvingError("Ollama model digest is invalid")
        if self.temperature < 0 or self.seed < 0 or self.top_k < 1:
            raise ProblemSolvingError("Ollama sampling bounds are invalid")
        if not 0 < self.top_p <= 1:
            raise ProblemSolvingError("Ollama top_p is invalid")
        if self.context_tokens < 1 or self.prediction_tokens < 1:
            raise ProblemSolvingError("Ollama token bounds must be positive")
        if self.timeout_seconds < 1 or self.computation_timeout_seconds < 1:
            raise ProblemSolvingError("proposal timeout must be positive")
        if not self.computation_runtime_identity:
            raise ProblemSolvingError(
                "computation runtime identity is required"
            )
        if not self.computation_dependency_lock_identity:
            raise ProblemSolvingError(
                "computation dependency lock identity is required"
            )
        computation_ids = tuple(
            problem_id
            for problem_id, _ in self.configured_computation_sources
        )
        if len(set(computation_ids)) != len(computation_ids):
            raise ProblemSolvingError(
                "configured computation identities must be unique"
            )
        if any(
            not problem_id or not source.strip()
            for problem_id, source in self.configured_computation_sources
        ):
            raise ProblemSolvingError(
                "configured computation sources must be complete"
            )
        if (
            self.archive_directory.is_symlink()
            or not self.archive_directory.is_dir()
        ):
            raise ProblemSolvingError(
                "proposal archive directory is unavailable or symlinked"
            )
        if self.archive_directory.stat().st_mode & 0o077:
            raise ProblemSolvingError(
                "proposal archive directory must have mode 0700"
            )


class OllamaSolutionProposalEngine:
    """Local, archived, proposal-only Ollama adapter."""

    def __init__(self, configuration: OllamaProposalConfiguration) -> None:
        self._configuration = configuration

    def propose(
        self,
        problem: ProblemMaterial,
        evidence: tuple[EvidencePassage, ...],
    ) -> SolutionDraft:
        runtime_version = self._validate_engine_identity()
        request_payload = self._request_payload(problem, evidence)
        request_bytes = self._canonical_bytes(request_payload)
        request_digest = hashlib.sha256(request_bytes).hexdigest()
        request_path = (
            self._configuration.archive_directory
            / f"{problem.problem_id}.{request_digest}.request.json"
        )
        self._write_or_confirm(request_path, request_bytes)

        response_path = (
            self._configuration.archive_directory
            / f"{problem.problem_id}.{request_digest}.response.json"
        )
        if response_path.exists() and not response_path.is_symlink():
            response_bytes = response_path.read_bytes()
        else:
            request = urllib.request.Request(
                f"{self._configuration.endpoint.rstrip('/')}/api/generate",
                data=request_bytes,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self._configuration.timeout_seconds,
                ) as response:
                    response_bytes = response.read()
            except Exception as error:
                raise ProblemSolvingError(
                    f"Ollama proposal request failed: {error}"
                ) from error
            self._write_or_confirm(response_path, response_bytes)

        try:
            response_payload = json.loads(response_bytes)
            if response_payload["model"] != self._configuration.model:
                raise ProblemSolvingError(
                    "Ollama response model does not match request"
                )
            output_channel = "response"
            proposal_text = response_payload.get("response")
            if not isinstance(proposal_text, str) or not proposal_text.strip():
                output_channel = "thinking"
                proposal_text = response_payload.get("thinking")
            if not isinstance(proposal_text, str) or not proposal_text.strip():
                raise ProblemSolvingError(
                    "Ollama response contains no proposal output"
                )
            proposal_payload = json.loads(proposal_text)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ProblemSolvingError(
                "Ollama response did not satisfy its output contract"
            ) from error
        draft = self._to_draft(problem, proposal_payload)
        self._validate_nondeterministic_boundary(problem, evidence, draft)
        receipt = {
            "schema_version": 1,
            "authority": "proposal_only",
            "problem_id": problem.problem_id,
            "model": self._configuration.model,
            "model_digest": self._configuration.model_digest,
            "ollama_runtime_version": runtime_version,
            "proposal_output_channel": output_channel,
            "system_prompt_sha256": (
                self._configuration.system_prompt_sha256
            ),
            "request_sha256": request_digest,
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "draft_sha256": hashlib.sha256(
                self._canonical_bytes(asdict(draft))
            ).hexdigest(),
            "seed": self._configuration.seed,
            "parameters": request_payload["options"],
            "computation_source": self._computation_source_receipt(problem),
            "input_identities": {
                "problem_statement_sha256": problem.statement_sha256,
                "evidence_passage_ids": [
                    passage.passage_id for passage in evidence
                ],
            },
        }
        receipt_path = (
            self._configuration.archive_directory
            / f"{problem.problem_id}.{request_digest}.receipt.json"
        )
        self._write_or_confirm(receipt_path, self._canonical_bytes(receipt))
        return draft

    def _validate_engine_identity(self) -> str:
        tags = self._get_json("/api/tags")
        matches = [
            item
            for item in tags.get("models", [])
            if item.get("name") == self._configuration.model
        ]
        if (
            len(matches) != 1
            or matches[0].get("digest")
            != self._configuration.model_digest
        ):
            raise ProblemSolvingError(
                "configured Ollama model identity is unavailable"
            )
        version_payload = self._get_json("/api/version")
        version = version_payload.get("version")
        if not isinstance(version, str):
            raise ProblemSolvingError("Ollama runtime version is unavailable")
        try:
            version_tuple = tuple(int(part) for part in version.split("."))
        except ValueError as error:
            raise ProblemSolvingError(
                "Ollama runtime version is invalid"
            ) from error
        if version_tuple < self._configuration.minimum_runtime_version:
            raise ProblemSolvingError("Ollama runtime version is too old")
        return version

    def _get_json(self, route: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._configuration.endpoint.rstrip('/')}{route}",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._configuration.timeout_seconds,
            ) as response:
                payload = json.load(response)
        except Exception as error:
            raise ProblemSolvingError(
                f"Ollama identity request failed: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise ProblemSolvingError("Ollama identity response is invalid")
        return payload

    def _request_payload(
        self,
        problem: ProblemMaterial,
        evidence: tuple[EvidencePassage, ...],
    ) -> dict[str, Any]:
        input_record = {
            "problem": asdict(problem),
            "evidence": [asdict(passage) for passage in evidence],
            "requirements": {
                "analytical_order": [
                    "givens",
                    "assumptions",
                    "governing_equations",
                    "derivation_steps",
                    "symbolic_result",
                    "substitutions",
                    "conclusion",
                ],
                "computation_required": (
                    problem.mode is ProblemMode.ANALYTICAL_COMPUTATIONAL
                ),
                "computation_source_authority": (
                    "configured_external"
                    if self._configured_computation_source(problem.problem_id)
                    is not None
                    else "model_proposal"
                ),
                "forbid_new_numeric_results": (
                    problem.mode is ProblemMode.ANALYTICAL_COMPUTATIONAL
                    and self._configured_computation_source(
                        problem.problem_id
                    )
                    is not None
                ),
                "computation_constraints": {
                    "allowed_imports": ["json", "math", "sympy"],
                    "single_json_stdout": True,
                    "network_access": False,
                },
                "status": "AUTOMATED_UNREVIEWED",
            },
        }
        return {
            "model": self._configuration.model,
            "system": self._configuration.system_prompt,
            "prompt": json.dumps(
                input_record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "format": self._output_schema(),
            "stream": False,
            "options": {
                "temperature": self._configuration.temperature,
                "seed": self._configuration.seed,
                "top_k": self._configuration.top_k,
                "top_p": self._configuration.top_p,
                "num_ctx": self._configuration.context_tokens,
                "num_predict": self._configuration.prediction_tokens,
            },
        }

    def _to_draft(
        self,
        problem: ProblemMaterial,
        payload: object,
    ) -> SolutionDraft:
        if not isinstance(payload, dict):
            raise ProblemSolvingError("proposal output must be an object")
        try:
            work = AnalyticalWork(
                givens=tuple(payload["givens"]),
                assumptions=tuple(payload["assumptions"]),
                governing_equations=tuple(payload["governing_equations"]),
                derivation_steps=tuple(payload["derivation_steps"]),
                symbolic_result=payload["symbolic_result"],
                substitutions=tuple(payload["substitutions"]),
                conclusion=payload["conclusion"],
            )
            source_code = payload["computation_source_code"]
            plan = None
            if problem.mode is ProblemMode.ANALYTICAL_COMPUTATIONAL:
                configured_source = self._configured_computation_source(
                    problem.problem_id
                )
                if configured_source is not None:
                    if source_code is not None:
                        raise ProblemSolvingError(
                            "proposal included code despite configured source"
                        )
                    selected_source = configured_source
                else:
                    if (
                        not isinstance(source_code, str)
                        or not source_code.strip()
                    ):
                        raise ProblemSolvingError(
                            "proposal omitted required computation source"
                        )
                    selected_source = source_code
                plan = ComputationPlan(
                    language="python",
                    source_code=selected_source,
                    runtime_identity=(
                        self._configuration.computation_runtime_identity
                    ),
                    dependency_lock_identity=(
                        self._configuration
                        .computation_dependency_lock_identity
                    ),
                    seed=self._configuration.seed,
                    timeout_seconds=(
                        self._configuration.computation_timeout_seconds
                    ),
                    network_access=False,
                )
            elif source_code is not None:
                raise ProblemSolvingError(
                    "proposal included an unauthorized computation"
                )
            return SolutionDraft(
                problem_id=problem.problem_id,
                evidence_passage_ids=tuple(
                    payload["evidence_passage_ids"]
                ),
                analytical_work=work,
                computation_plan=plan,
            )
        except (KeyError, TypeError) as error:
            raise ProblemSolvingError(
                "proposal output did not satisfy its domain contract"
            ) from error

    def _validate_nondeterministic_boundary(
        self,
        problem: ProblemMaterial,
        evidence: tuple[EvidencePassage, ...],
        draft: SolutionDraft,
    ) -> None:
        if (
            problem.mode is not ProblemMode.ANALYTICAL_COMPUTATIONAL
            or self._configured_computation_source(problem.problem_id) is None
        ):
            return
        source_text = "\n".join(
            (problem.statement, *(passage.text for passage in evidence))
        )
        allowed = self._numeric_literals(source_text)
        work = draft.analytical_work
        proposed_text = "\n".join(
            (
                work.symbolic_result or "",
                work.conclusion,
            )
        )
        introduced = self._numeric_literals(proposed_text) - allowed
        if introduced:
            raise ProblemSolvingError(
                "proposal performed numerical evaluation outside the "
                "non-deterministic boundary"
            )

    @staticmethod
    def _numeric_literals(text: str) -> set[Decimal]:
        values: set[Decimal] = set()
        for match in _NUMERIC_LITERAL_RE.finditer(text):
            try:
                values.add(Decimal(match.group(0)))
            except InvalidOperation as error:
                raise ProblemSolvingError(
                    "proposal contained an invalid numeric literal"
                ) from error
        return values

    def _configured_computation_source(
        self,
        problem_id: str,
    ) -> str | None:
        return dict(self._configuration.configured_computation_sources).get(
            problem_id
        )

    def _computation_source_receipt(
        self,
        problem: ProblemMaterial,
    ) -> dict[str, object]:
        source = self._configured_computation_source(problem.problem_id)
        if source is None:
            return {"authority": "model_proposal", "sha256": None}
        return {
            "authority": "configured_external",
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }

    @staticmethod
    def _canonical_bytes(value: object) -> bytes:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _write_or_confirm(path: Path, payload: bytes) -> None:
        if path.is_symlink():
            raise ProblemSolvingError(
                f"proposal archive symlink is forbidden: {path.name}"
            )
        if path.exists():
            if path.read_bytes() != payload:
                raise ProblemSolvingError(
                    f"proposal archive collision: {path.name}"
                )
            return
        temporary = path.with_name(f".{path.name}.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ProblemSolvingError(
                f"proposal temporary collision: {temporary.name}"
            )
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    @staticmethod
    def _output_schema() -> dict[str, Any]:
        string_array = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "evidence_passage_ids",
                "givens",
                "assumptions",
                "governing_equations",
                "derivation_steps",
                "symbolic_result",
                "substitutions",
                "conclusion",
                "computation_source_code",
            ],
            "properties": {
                "evidence_passage_ids": string_array,
                "givens": string_array,
                "assumptions": string_array,
                "governing_equations": string_array,
                "derivation_steps": string_array,
                "symbolic_result": {"type": ["string", "null"]},
                "substitutions": string_array,
                "conclusion": {"type": "string"},
                "computation_source_code": {
                    "type": ["string", "null"]
                },
            },
        }
