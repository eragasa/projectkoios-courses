from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from .models import ProblemSolvingError, ReviewStatus, text_sha256

_GIVEN_LEXEME_RE = re.compile(
    r"^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$"
)
_PROBLEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REQUIRED_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "given_lexeme",
        "quantity_kind",
        "source_unit",
        "target_unit",
        "assumption_ids",
    }
)


class _OllamaHttpResponseError(Exception):
    def __init__(self, status: int, response_bytes: bytes) -> None:
        super().__init__(f"Ollama returned HTTP {status}")
        self.status = status
        self.response_bytes = response_bytes


class ConversionQuantityKind(StrEnum):
    SPEED = "speed"


class ConversionUnit(StrEnum):
    MILE_PER_HOUR = "mile_per_hour"
    METRE_PER_SECOND = "metre_per_second"


class ConversionAssumption(StrEnum):
    NON_NEGATIVE_SCALAR_SPEED = "non_negative_scalar_speed"


@dataclass(frozen=True)
class ConversionIntentProblem:
    problem_id: str
    statement: str
    statement_sha256: str
    candidate_given_lexeme: str

    def __post_init__(self) -> None:
        if _PROBLEM_ID_RE.fullmatch(self.problem_id) is None:
            raise ProblemSolvingError("conversion problem identity is invalid")
        if self.statement_sha256 != text_sha256(self.statement):
            raise ProblemSolvingError("conversion statement identity mismatch")
        statement_bytes = self.statement.encode("utf-8")
        if not statement_bytes or len(statement_bytes) > 512:
            raise ProblemSolvingError(
                "conversion statement exceeds its UTF-8 bound"
            )
        if "\r" in self.statement or _CONTROL_RE.search(self.statement):
            raise ProblemSolvingError(
                "conversion statement contains prohibited characters"
            )
        _validate_given_lexeme(self.candidate_given_lexeme)
        if self.candidate_given_lexeme not in self.statement:
            raise ProblemSolvingError(
                "candidate given lexeme is absent from the statement"
            )


@dataclass(frozen=True)
class ConversionIntentProposal:
    problem_id: str
    schema: str
    given_lexeme: str
    quantity_kind: ConversionQuantityKind
    source_unit: ConversionUnit
    target_unit: ConversionUnit
    assumption_ids: tuple[ConversionAssumption, ...]
    review_status: ReviewStatus = ReviewStatus.AUTOMATED_UNREVIEWED

    def __post_init__(self) -> None:
        if self.schema != "koios.conversion-intent.v1":
            raise ProblemSolvingError("conversion proposal schema is invalid")
        if self.quantity_kind is not ConversionQuantityKind.SPEED:
            raise ProblemSolvingError("conversion quantity kind is invalid")
        if self.source_unit is not ConversionUnit.MILE_PER_HOUR:
            raise ProblemSolvingError("conversion source unit is invalid")
        if self.target_unit is not ConversionUnit.METRE_PER_SECOND:
            raise ProblemSolvingError("conversion target unit is invalid")
        if self.assumption_ids != (
            ConversionAssumption.NON_NEGATIVE_SCALAR_SPEED,
        ):
            raise ProblemSolvingError("conversion assumptions are invalid")
        if self.review_status is not ReviewStatus.AUTOMATED_UNREVIEWED:
            raise ProblemSolvingError(
                "conversion proposals must remain AUTOMATED_UNREVIEWED"
            )
        _validate_given_lexeme(self.given_lexeme)

    @property
    def identity(self) -> str:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return (
            "conversion-intent-proposal:sha256:"
            f"{hashlib.sha256(payload).hexdigest()}"
        )


@dataclass(frozen=True)
class OllamaConversionIntentConfiguration:
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
    archive_directory: Path

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
        if self.temperature != 0 or self.seed < 0 or self.top_k != 1:
            raise ProblemSolvingError(
                "conversion proposal sampling must be deterministic"
            )
        if self.top_p != 1.0:
            raise ProblemSolvingError(
                "conversion proposal top_p must equal one"
            )
        if self.context_tokens < 1 or not 1 <= self.prediction_tokens <= 96:
            raise ProblemSolvingError(
                "conversion proposal token bounds are invalid"
            )
        if self.timeout_seconds < 1:
            raise ProblemSolvingError(
                "conversion proposal timeout must be positive"
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


class OllamaConversionIntentProposalEngine:
    """Archive one bounded, non-authoritative conversion-intent proposal."""

    def __init__(
        self,
        configuration: OllamaConversionIntentConfiguration,
    ) -> None:
        self._configuration = configuration

    def propose(
        self,
        problem: ConversionIntentProblem,
    ) -> ConversionIntentProposal:
        runtime_version = self._validate_engine_identity()
        request_payload = self._request_payload(problem)
        request_bytes = self._canonical_bytes(request_payload)
        request_digest = hashlib.sha256(request_bytes).hexdigest()
        stem = f"{problem.problem_id}.{request_digest}"
        request_path = (
            self._configuration.archive_directory / f"{stem}.request.json"
        )
        response_path = (
            self._configuration.archive_directory / f"{stem}.response.json"
        )
        receipt_path = (
            self._configuration.archive_directory / f"{stem}.receipt.json"
        )
        self._write_or_confirm(request_path, request_bytes)

        if response_path.exists() and not response_path.is_symlink():
            response_bytes = response_path.read_bytes()
        else:
            try:
                response_bytes = self._generate(request_bytes)
            except _OllamaHttpResponseError as error:
                self._write_or_confirm(
                    response_path,
                    error.response_bytes,
                )
                response_sha256 = hashlib.sha256(
                    error.response_bytes
                ).hexdigest()
                raise ProblemSolvingError(
                    "Ollama proposal request failed with HTTP "
                    f"{error.status}; response sha256:{response_sha256}"
                ) from error
            self._write_or_confirm(response_path, response_bytes)

        response_payload = self._strict_json_bytes(
            response_bytes,
            maximum_bytes=131_072,
            label="Ollama response envelope",
        )
        if not isinstance(response_payload, dict):
            raise ProblemSolvingError(
                "Ollama response envelope must be an object"
            )
        if response_payload.get("model") != self._configuration.model:
            raise ProblemSolvingError(
                "Ollama response model does not match request"
            )
        output_channel, proposal_text = self._proposal_text(response_payload)
        proposal_payload = self._strict_proposal_payload(proposal_text)
        proposal = self._to_proposal(problem, proposal_payload)
        receipt = {
            "schema_version": 1,
            "authority": "proposal_only",
            "problem_id": problem.problem_id,
            "proposal_id": proposal.identity,
            "review_status": proposal.review_status,
            "model": self._configuration.model,
            "model_digest": self._configuration.model_digest,
            "ollama_runtime_version": runtime_version,
            "proposal_output_channel": output_channel,
            "system_prompt_sha256": (
                self._configuration.system_prompt_sha256
            ),
            "request_sha256": request_digest,
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "proposal_sha256": hashlib.sha256(
                proposal_text.encode("ascii")
            ).hexdigest(),
            "seed": self._configuration.seed,
            "parameters": request_payload["options"],
            "input_identities": {
                "statement_sha256": problem.statement_sha256,
                "candidate_given_lexeme_sha256": hashlib.sha256(
                    problem.candidate_given_lexeme.encode("ascii")
                ).hexdigest(),
            },
            "prohibited_outputs": [
                "arithmetic",
                "code",
                "conversion_relation",
                "intermediate_result",
                "final_result",
                "tolerance",
                "verification_claim",
            ],
        }
        self._write_or_confirm(receipt_path, self._canonical_bytes(receipt))
        return proposal

    def _generate(self, request_bytes: bytes) -> bytes:
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
                response_bytes = response.read(131_073)
        except urllib.error.HTTPError as error:
            response_bytes = error.read(131_073)
            if len(response_bytes) > 131_072:
                raise ProblemSolvingError(
                    "Ollama error response exceeds its byte limit"
                ) from error
            raise _OllamaHttpResponseError(
                error.code,
                response_bytes,
            ) from error
        except Exception as error:
            raise ProblemSolvingError(
                f"Ollama proposal request failed: {error}"
            ) from error
        if len(response_bytes) > 131_072:
            raise ProblemSolvingError(
                "Ollama response envelope exceeds its byte limit"
            )
        return cast(bytes, response_bytes)

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
        problem: ConversionIntentProblem,
    ) -> dict[str, Any]:
        input_record = {
            "problem_id": problem.problem_id,
            "statement": problem.statement,
            "candidate_given_lexeme": problem.candidate_given_lexeme,
            "instruction": (
                "Classify only the requested conversion intent. Copy the "
                "candidate lexeme exactly. Do not calculate."
            ),
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

    @staticmethod
    def _proposal_text(
        response_payload: dict[str, Any],
    ) -> tuple[str, str]:
        for channel in ("response", "thinking"):
            value = response_payload.get(channel)
            if isinstance(value, str) and value.strip():
                return channel, value
        raise ProblemSolvingError(
            "Ollama response contains no proposal output"
        )

    def _strict_proposal_payload(self, text: str) -> dict[str, object]:
        try:
            encoded = text.encode("ascii")
        except UnicodeEncodeError as error:
            raise ProblemSolvingError(
                "conversion proposal must contain ASCII only"
            ) from error
        payload = self._strict_json_bytes(
            encoded,
            maximum_bytes=256,
            label="conversion proposal",
        )
        if not isinstance(payload, dict):
            raise ProblemSolvingError(
                "conversion proposal must be one JSON object"
            )
        if set(payload) != _REQUIRED_RESPONSE_KEYS:
            raise ProblemSolvingError(
                "conversion proposal has missing or unknown fields"
            )
        if any(
            isinstance(value, (bool, int, float)) or value is None
            for value in payload.values()
        ):
            raise ProblemSolvingError(
                "conversion proposal contains a prohibited JSON value"
            )
        return payload

    @staticmethod
    def _strict_json_bytes(
        payload: bytes,
        *,
        maximum_bytes: int,
        label: str,
    ) -> object:
        if len(payload) > maximum_bytes:
            raise ProblemSolvingError(f"{label} exceeds its byte limit")
        try:
            text = payload.decode("utf-8", errors="strict")

            def reject_duplicates(
                pairs: list[tuple[str, object]],
            ) -> dict[str, object]:
                result: dict[str, object] = {}
                for key, value in pairs:
                    if key in result:
                        raise ProblemSolvingError(
                            f"{label} contains duplicate object members"
                        )
                    result[key] = value
                return result

            return json.loads(text, object_pairs_hook=reject_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProblemSolvingError(f"{label} is invalid JSON") from error

    @staticmethod
    def _to_proposal(
        problem: ConversionIntentProblem,
        payload: dict[str, object],
    ) -> ConversionIntentProposal:
        string_keys = (
            "schema",
            "given_lexeme",
            "quantity_kind",
            "source_unit",
            "target_unit",
        )
        try:
            string_values = tuple(payload[key] for key in string_keys)
            assumption_ids = payload["assumption_ids"]
        except KeyError as error:
            raise ProblemSolvingError(
                "conversion proposal has a missing value"
            ) from error
        if any(not isinstance(value, str) for value in string_values):
            raise ProblemSolvingError(
                "conversion proposal scalar values must be strings"
            )
        if not isinstance(assumption_ids, list):
            raise ProblemSolvingError(
                "conversion proposal assumptions must be an array"
            )
        if any(not isinstance(item, str) for item in assumption_ids):
            raise ProblemSolvingError(
                "conversion proposal assumptions must be strings"
            )
        schema = cast(str, string_values[0])
        lexeme = cast(str, string_values[1])
        quantity = cast(str, string_values[2])
        source = cast(str, string_values[3])
        target = cast(str, string_values[4])
        try:
            quantity_kind = ConversionQuantityKind(quantity)
            source_unit = ConversionUnit(source)
            target_unit = ConversionUnit(target)
            assumptions = tuple(
                ConversionAssumption(item) for item in assumption_ids
            )
        except ValueError as error:
            raise ProblemSolvingError(
                "conversion proposal has a disallowed value"
            ) from error
        proposal = ConversionIntentProposal(
            problem_id=problem.problem_id,
            schema=schema,
            given_lexeme=lexeme,
            quantity_kind=quantity_kind,
            source_unit=source_unit,
            target_unit=target_unit,
            assumption_ids=assumptions,
        )
        if proposal.given_lexeme != problem.candidate_given_lexeme:
            raise ProblemSolvingError(
                "conversion proposal changed the candidate given lexeme"
            )
        return proposal

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
        return {
            "type": "object",
            "additionalProperties": False,
            "required": sorted(_REQUIRED_RESPONSE_KEYS),
            "properties": {
                "schema": {
                    "type": "string",
                    "const": "koios.conversion-intent.v1",
                },
                "given_lexeme": {
                    "type": "string",
                    "pattern": _GIVEN_LEXEME_RE.pattern,
                    "maxLength": 14,
                },
                "quantity_kind": {
                    "type": "string",
                    "const": ConversionQuantityKind.SPEED,
                },
                "source_unit": {
                    "type": "string",
                    "const": ConversionUnit.MILE_PER_HOUR,
                },
                "target_unit": {
                    "type": "string",
                    "const": ConversionUnit.METRE_PER_SECOND,
                },
                "assumption_ids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "const": (
                            ConversionAssumption
                            .NON_NEGATIVE_SCALAR_SPEED
                        ),
                    },
                    "minItems": 1,
                    "maxItems": 1,
                    "uniqueItems": True,
                },
            },
        }


def _validate_given_lexeme(value: str) -> None:
    if _GIVEN_LEXEME_RE.fullmatch(value) is None:
        raise ProblemSolvingError("candidate given lexeme is invalid")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise ProblemSolvingError(
            "candidate given lexeme is invalid"
        ) from error
    if decimal_value < 0 or decimal_value > Decimal("1000000.000000"):
        raise ProblemSolvingError("candidate given lexeme is outside bounds")
