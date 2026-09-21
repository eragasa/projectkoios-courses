from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .conversion_intent import (
    ConversionIntentProblem,
    OllamaConversionIntentConfiguration,
    OllamaConversionIntentProposalEngine,
)
from .models import ProblemSolvingError

_INPUT_SCHEMA = "koios.conversion-intent-pilot-input.v1"
_SUMMARY_SCHEMA = "koios.conversion-intent-pilot-summary.v1"
_INPUT_KEYS = frozenset({"schema", "problems"})
_PROBLEM_KEYS = frozenset(
    {
        "problem_id",
        "statement",
        "statement_sha256",
        "candidate_given_lexeme",
    }
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        input_path = _safe_file(arguments.input, maximum_bytes=16_384)
        prompt_path = _safe_file(
            arguments.system_prompt,
            maximum_bytes=8_192,
        )
        archive = _safe_directory(arguments.archive_directory)
        problems = _load_problems(input_path)
        system_prompt = prompt_path.read_text(encoding="utf-8")
        configuration = OllamaConversionIntentConfiguration(
            endpoint=arguments.endpoint,
            model=arguments.model,
            model_digest=arguments.model_digest,
            minimum_runtime_version=_runtime_version(
                arguments.minimum_runtime_version
            ),
            system_prompt=system_prompt,
            system_prompt_sha256=hashlib.sha256(
                system_prompt.encode("utf-8")
            ).hexdigest(),
            temperature=0.0,
            seed=arguments.seed,
            top_k=1,
            top_p=1.0,
            context_tokens=arguments.context_tokens,
            prediction_tokens=arguments.prediction_tokens,
            timeout_seconds=arguments.timeout_seconds,
            archive_directory=archive,
        )
        engine = OllamaConversionIntentProposalEngine(configuration)
        outcomes: list[dict[str, object]] = []
        for problem in problems:
            try:
                proposal = engine.propose(problem)
            except ProblemSolvingError as error:
                outcomes.append(
                    {
                        "problem_id": problem.problem_id,
                        "outcome": "rejected",
                        "error": str(error),
                        "proposal_id": None,
                    }
                )
                continue
            outcomes.append(
                {
                    "problem_id": problem.problem_id,
                    "outcome": "proposed",
                    "error": None,
                    "proposal_id": proposal.identity,
                    "schema": proposal.schema,
                    "given_lexeme": proposal.given_lexeme,
                    "quantity_kind": proposal.quantity_kind,
                    "source_unit": proposal.source_unit,
                    "target_unit": proposal.target_unit,
                    "assumption_ids": proposal.assumption_ids,
                    "review_status": proposal.review_status,
                }
            )
        summary = {
            "schema": _SUMMARY_SCHEMA,
            "authority": "proposal_only",
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "system_prompt_sha256": configuration.system_prompt_sha256,
            "model": configuration.model,
            "model_digest": configuration.model_digest,
            "problem_count": len(problems),
            "proposed_count": sum(
                item["outcome"] == "proposed" for item in outcomes
            ),
            "rejected_count": sum(
                item["outcome"] == "rejected" for item in outcomes
            ),
            "deterministic_computation_performed": False,
            "solution_generated": False,
            "published": False,
            "outcomes": outcomes,
        }
        _atomic_write(
            arguments.summary_output,
            _canonical_bytes(summary),
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["rejected_count"] == 0 else 1
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(str(error))
    except ProblemSolvingError as error:
        parser.error(str(error))
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run bounded Ollama conversion-intent proposals."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--system-prompt", type=Path, required=True)
    parser.add_argument("--archive-directory", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--minimum-runtime-version", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--context-tokens", type=int, required=True)
    parser.add_argument("--prediction-tokens", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, required=True)
    return parser


def _safe_file(path: Path, *, maximum_bytes: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ProblemSolvingError(f"input file is unavailable: {path.name}")
    if path.stat().st_size > maximum_bytes:
        raise ProblemSolvingError(f"input file exceeds limit: {path.name}")
    if path.stat().st_mode & 0o077:
        raise ProblemSolvingError(
            f"input file must have mode 0600: {path.name}"
        )
    return path


def _safe_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ProblemSolvingError(
            f"archive directory is unavailable: {path.name}"
        )
    if path.stat().st_mode & 0o077:
        raise ProblemSolvingError(
            f"archive directory must have mode 0700: {path.name}"
        )
    return path


def _load_problems(path: Path) -> tuple[ConversionIntentProblem, ...]:
    payload = _strict_json(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != _INPUT_KEYS:
        raise ProblemSolvingError("pilot input has missing or unknown fields")
    if payload["schema"] != _INPUT_SCHEMA:
        raise ProblemSolvingError("pilot input schema is invalid")
    records = payload["problems"]
    if not isinstance(records, list) or not 1 <= len(records) <= 32:
        raise ProblemSolvingError("pilot problem count is invalid")
    problems: list[ConversionIntentProblem] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != _PROBLEM_KEYS:
            raise ProblemSolvingError(
                "pilot problem has missing or unknown fields"
            )
        if any(not isinstance(value, str) for value in record.values()):
            raise ProblemSolvingError("pilot problem values must be strings")
        problems.append(
            ConversionIntentProblem(
                problem_id=record["problem_id"],
                statement=record["statement"],
                statement_sha256=record["statement_sha256"],
                candidate_given_lexeme=record["candidate_given_lexeme"],
            )
        )
    identities = [problem.problem_id for problem in problems]
    if len(identities) != len(set(identities)):
        raise ProblemSolvingError("pilot problem identities must be unique")
    return tuple(problems)


def _strict_json(payload: bytes) -> object:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ProblemSolvingError("pilot input must not contain a BOM")

    def reject_duplicates(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProblemSolvingError(
                    "pilot input contains duplicate object members"
                )
            result[key] = value
        return result

    return json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=reject_duplicates,
    )


def _runtime_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3:
        raise ProblemSolvingError("minimum runtime version is invalid")
    try:
        version = tuple(int(part) for part in parts)
    except ValueError as error:
        raise ProblemSolvingError(
            "minimum runtime version is invalid"
        ) from error
    if any(part < 0 for part in version):
        raise ProblemSolvingError("minimum runtime version is invalid")
    return version[0], version[1], version[2]


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


def _atomic_write(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise ProblemSolvingError(
            f"summary destination already exists: {path.name}"
        )
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ProblemSolvingError(
            f"summary directory is unavailable: {parent.name}"
        )
    if parent.stat().st_mode & 0o077:
        raise ProblemSolvingError(
            f"summary directory must have mode 0700: {parent.name}"
        )
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ProblemSolvingError(
            f"summary temporary collision: {temporary.name}"
        )
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


if __name__ == "__main__":
    raise SystemExit(main())
