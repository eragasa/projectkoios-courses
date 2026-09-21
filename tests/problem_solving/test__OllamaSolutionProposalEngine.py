from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving import (
    CorpusRole,
    EvidencePassage,
    OllamaProposalConfiguration,
    OllamaSolutionProposalEngine,
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    RetrievalPurpose,
    SourceSpan,
    text_sha256,
)

PROMPT = "Propose a source-grounded solution."
MODEL_DIGEST = "a" * 64


class JsonResponse(io.BytesIO):
    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def response(value: object) -> JsonResponse:
    return JsonResponse(json.dumps(value).encode("utf-8"))


def configuration(directory: Path) -> OllamaProposalConfiguration:
    directory.chmod(0o700)
    return OllamaProposalConfiguration(
        endpoint="http://127.0.0.1:11434",
        model="test-model:1",
        model_digest=MODEL_DIGEST,
        minimum_runtime_version=(0, 34, 2),
        system_prompt=PROMPT,
        system_prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
        temperature=0.0,
        seed=20260921,
        top_k=1,
        top_p=1.0,
        context_tokens=4096,
        prediction_tokens=1024,
        timeout_seconds=30,
        computation_runtime_identity="python:test",
        computation_dependency_lock_identity="lock:test",
        computation_timeout_seconds=5,
        archive_directory=directory,
    )


def problem() -> ProblemMaterial:
    statement = "Why is entropy evaluated on a reversible path?"
    return ProblemMaterial(
        problem_id="book.20.Q.1",
        statement=statement,
        statement_sha256=text_sha256(statement),
        mode=ProblemMode.CONCEPTUAL,
        source=SourceSpan("source:sha256:" + "b" * 64, 10, 10),
    )


def evidence() -> tuple[EvidencePassage, ...]:
    return (
        EvidencePassage(
            passage_id="evidence:1",
            text="Entropy is a state function.",
            source=SourceSpan("source:sha256:" + "b" * 64, 8, 9),
            corpus_role=CorpusRole.THEORY_EVIDENCE,
            admitted_purposes=(RetrievalPurpose.PROBLEM_SOLVING,),
        ),
    )


def proposal_response() -> dict[str, object]:
    output = {
        "evidence_passage_ids": ["evidence:1"],
        "givens": [],
        "assumptions": [],
        "governing_equations": [],
        "derivation_steps": [],
        "symbolic_result": None,
        "substitutions": [],
        "conclusion": "The relation requires a reversible path.",
        "computation_source_code": None,
    }
    return {
        "model": "test-model:1",
        "response": json.dumps(output),
        "done": True,
    }


def test__propose__archives_request_response_and_receipt(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    engine = OllamaSolutionProposalEngine(configuration(archive))
    responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
        response(proposal_response()),
    )

    with patch("urllib.request.urlopen", side_effect=responses):
        draft = engine.propose(problem(), evidence())

    assert draft.problem_id == "book.20.Q.1"
    assert draft.evidence_passage_ids == ("evidence:1",)
    assert draft.computation_plan is None
    artifacts = sorted(archive.iterdir())
    assert len(artifacts) == 3
    assert {path.suffixes[-2] for path in artifacts} == {
        ".receipt",
        ".request",
        ".response",
    }
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in artifacts)
    receipt_path = next(
        path for path in artifacts if path.name.endswith(".receipt.json")
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["authority"] == "proposal_only"
    assert receipt["model_digest"] == MODEL_DIGEST
    assert receipt["ollama_runtime_version"] == "0.34.2"

    replay_identity_responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
    )
    with patch(
        "urllib.request.urlopen", side_effect=replay_identity_responses
    ) as urlopen:
        replay = engine.propose(problem(), evidence())

    assert replay == draft
    assert urlopen.call_count == 2
    assert len(tuple(archive.iterdir())) == 3


def test__propose__accepts_structured_output_in_thinking_channel(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    engine = OllamaSolutionProposalEngine(configuration(archive))
    generated = proposal_response()
    generated["thinking"] = generated["response"]
    generated["response"] = ""
    responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
        response(generated),
    )

    with patch("urllib.request.urlopen", side_effect=responses):
        draft = engine.propose(problem(), evidence())

    assert draft.problem_id == "book.20.Q.1"
    receipt_path = next(archive.glob("*.receipt.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["proposal_output_channel"] == "thinking"


def test__propose__uses_configured_external_computation_source(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    analytical_problem = replace(
        problem(),
        mode=ProblemMode.ANALYTICAL_COMPUTATIONAL,
    )
    computation_source = (
        'import json\nprint(json.dumps({"result_summary": "ok", '
        '"analytical_agreement": True, "findings": []}))\n'
    )
    configured = replace(
        configuration(archive),
        configured_computation_sources=(
            (analytical_problem.problem_id, computation_source),
        ),
    )
    engine = OllamaSolutionProposalEngine(configured)
    generated = proposal_response()
    responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
        response(generated),
    )

    with patch("urllib.request.urlopen", side_effect=responses):
        draft = engine.propose(analytical_problem, evidence())

    assert draft.computation_plan is not None
    assert draft.computation_plan.source_code == computation_source
    request_path = next(archive.glob("*.request.json"))
    request_record = json.loads(request_path.read_text())
    prompt_record = json.loads(request_record["prompt"])
    assert prompt_record["requirements"][
        "computation_source_authority"
    ] == "configured_external"
    receipt_path = next(archive.glob("*.receipt.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["computation_source"] == {
        "authority": "configured_external",
        "sha256": hashlib.sha256(computation_source.encode()).hexdigest(),
    }


def test__propose__rejects_model_code_when_external_source_is_configured(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    analytical_problem = replace(
        problem(),
        mode=ProblemMode.ANALYTICAL_COMPUTATIONAL,
    )
    configured = replace(
        configuration(archive),
        configured_computation_sources=(
            (analytical_problem.problem_id, "print('{}')\n"),
        ),
    )
    engine = OllamaSolutionProposalEngine(configured)
    generated = proposal_response()
    assert isinstance(generated["response"], str)
    proposal = json.loads(generated["response"])
    proposal["computation_source_code"] = "print('{}')\n"
    generated["response"] = json.dumps(proposal)
    responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
        response(generated),
    )

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(ProblemSolvingError, match="despite configured source"),
    ):
        engine.propose(analytical_problem, evidence())


def test__propose__rejects_new_numeric_result_from_model(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    analytical_problem = replace(
        problem(),
        mode=ProblemMode.ANALYTICAL_COMPUTATIONAL,
    )
    configured = replace(
        configuration(archive),
        configured_computation_sources=(
            (analytical_problem.problem_id, "print('{}')\n"),
        ),
    )
    engine = OllamaSolutionProposalEngine(configured)
    generated = proposal_response()
    assert isinstance(generated["response"], str)
    proposal = json.loads(generated["response"])
    proposal["symbolic_result"] = "Q = 12.5 J"
    proposal["conclusion"] = "The computed result is 12.5 J."
    generated["response"] = json.dumps(proposal)
    responses = (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
        response(generated),
    )

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(
            ProblemSolvingError,
            match="outside the non-deterministic boundary",
        ),
    ):
        engine.propose(analytical_problem, evidence())


def test__configuration__rejects_nonlocal_endpoint(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    values = configuration(archive).__dict__ | {
        "endpoint": "https://example.invalid"
    }

    with pytest.raises(ProblemSolvingError, match="endpoint must be local"):
        OllamaProposalConfiguration(**values)


def test__propose__rejects_model_identity_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    engine = OllamaSolutionProposalEngine(configuration(archive))

    with (
        patch(
            "urllib.request.urlopen",
            return_value=response(
                {
                    "models": [
                        {"name": "test-model:1", "digest": "c" * 64}
                    ]
                }
            ),
        ),
        pytest.raises(ProblemSolvingError, match="model identity"),
    ):
        engine.propose(problem(), evidence())
