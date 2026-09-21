from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest
from projectkoios.courses.problem_solving import (
    ConversionAssumption,
    ConversionIntentProblem,
    ConversionQuantityKind,
    ConversionUnit,
    OllamaConversionIntentConfiguration,
    OllamaConversionIntentProposalEngine,
    ProblemSolvingError,
    ReviewStatus,
    text_sha256,
)

PROMPT = (
    "Return only the requested conversion-intent JSON object. "
    "Copy the supplied candidate lexeme exactly. Never calculate."
)
MODEL_DIGEST = "a" * 64


class JsonResponse(io.BytesIO):
    def __enter__(self) -> JsonResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def response(value: object) -> JsonResponse:
    return JsonResponse(json.dumps(value).encode("utf-8"))


def configuration(
    directory: Path,
) -> OllamaConversionIntentConfiguration:
    directory.chmod(0o700)
    return OllamaConversionIntentConfiguration(
        endpoint="http://127.0.0.1:11434",
        model="test-model:1",
        model_digest=MODEL_DIGEST,
        minimum_runtime_version=(0, 34, 2),
        system_prompt=PROMPT,
        system_prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
        temperature=0.0,
        seed=0,
        top_k=1,
        top_p=1.0,
        context_tokens=2048,
        prediction_tokens=96,
        timeout_seconds=30,
        archive_directory=directory,
    )


def problem() -> ConversionIntentProblem:
    statement = "Convert the speed 55.0 miles per hour to metres per second."
    return ConversionIntentProblem(
        problem_id="synthetic.speed.1",
        statement=statement,
        statement_sha256=text_sha256(statement),
        candidate_given_lexeme="55.0",
    )


def proposal_payload() -> dict[str, object]:
    return {
        "schema": "koios.conversion-intent.v1",
        "given_lexeme": "55.0",
        "quantity_kind": "speed",
        "source_unit": "mile_per_hour",
        "target_unit": "metre_per_second",
        "assumption_ids": ["non_negative_scalar_speed"],
    }


def model_response(payload: object) -> dict[str, object]:
    return {
        "model": "test-model:1",
        "response": json.dumps(payload, separators=(",", ":")),
        "done": True,
    }


def identity_responses() -> tuple[JsonResponse, JsonResponse]:
    return (
        response(
            {
                "models": [
                    {"name": "test-model:1", "digest": MODEL_DIGEST}
                ]
            }
        ),
        response({"version": "0.34.2"}),
    )


def test__propose__archives_strict_intent_without_computation(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    engine = OllamaConversionIntentProposalEngine(configuration(archive))
    responses = (
        *identity_responses(),
        response(model_response(proposal_payload())),
    )

    with patch("urllib.request.urlopen", side_effect=responses):
        proposal = engine.propose(problem())

    assert proposal.given_lexeme == "55.0"
    assert proposal.quantity_kind is ConversionQuantityKind.SPEED
    assert proposal.source_unit is ConversionUnit.MILE_PER_HOUR
    assert proposal.target_unit is ConversionUnit.METRE_PER_SECOND
    assert proposal.assumption_ids == (
        ConversionAssumption.NON_NEGATIVE_SCALAR_SPEED,
    )
    assert proposal.review_status is ReviewStatus.AUTOMATED_UNREVIEWED
    assert proposal.identity.startswith(
        "conversion-intent-proposal:sha256:"
    )

    artifacts = sorted(archive.iterdir())
    assert len(artifacts) == 3
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in artifacts)
    request_path = next(archive.glob("*.request.json"))
    request_record = json.loads(request_path.read_text())
    prompt_record = json.loads(request_record["prompt"])
    assumptions_schema = request_record["format"]["properties"][
        "assumption_ids"
    ]
    assert isinstance(assumptions_schema["items"], dict)
    assert assumptions_schema["uniqueItems"] is True
    assert set(prompt_record) == {
        "candidate_given_lexeme",
        "instruction",
        "problem_id",
        "statement",
    }
    request_text = json.dumps(request_record, sort_keys=True)
    for prohibited in (
        "evidence_passage",
        "source_path",
        "conversion_relation",
        "expected_answer",
        "computation",
    ):
        assert prohibited not in request_text
    receipt = json.loads(next(archive.glob("*.receipt.json")).read_text())
    assert receipt["authority"] == "proposal_only"
    assert receipt["review_status"] == "AUTOMATED_UNREVIEWED"
    assert "final_result" in receipt["prohibited_outputs"]

    replay_responses = identity_responses()
    with patch(
        "urllib.request.urlopen",
        side_effect=replay_responses,
    ) as urlopen:
        replay = engine.propose(problem())

    assert replay == proposal
    assert urlopen.call_count == 2
    assert len(tuple(archive.iterdir())) == 3


def test__propose__accepts_exact_json_from_thinking_channel(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    generated = model_response(proposal_payload())
    generated["thinking"] = generated["response"]
    generated["response"] = ""
    responses = (*identity_responses(), response(generated))
    engine = OllamaConversionIntentProposalEngine(configuration(archive))

    with patch("urllib.request.urlopen", side_effect=responses):
        proposal = engine.propose(problem())

    assert proposal.given_lexeme == "55.0"
    receipt = json.loads(next(archive.glob("*.receipt.json")).read_text())
    assert receipt["proposal_output_channel"] == "thinking"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"result": "24.5872"}, "missing or unknown fields"),
        ({"source_unit": "kilometre_per_hour"}, "disallowed value"),
        ({"given_lexeme": "55"}, "changed the candidate"),
        ({"assumption_ids": []}, "assumptions are invalid"),
    ],
)
def test__propose__rejects_values_outside_closed_contract(
    tmp_path: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    payload = proposal_payload() | mutation
    responses = (*identity_responses(), response(model_response(payload)))
    engine = OllamaConversionIntentProposalEngine(configuration(archive))

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(ProblemSolvingError, match=message),
    ):
        engine.propose(problem())

    assert not tuple(archive.glob("*.receipt.json"))


def test__propose__rejects_duplicate_members(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    duplicate = (
        '{"schema":"koios.conversion-intent.v1",'
        '"schema":"koios.conversion-intent.v1",'
        '"given_lexeme":"55.0","quantity_kind":"speed",'
        '"source_unit":"mile_per_hour",'
        '"target_unit":"metre_per_second",'
        '"assumption_ids":["non_negative_scalar_speed"]}'
    )
    generated = {
        "model": "test-model:1",
        "response": duplicate,
        "done": True,
    }
    responses = (*identity_responses(), response(generated))
    engine = OllamaConversionIntentProposalEngine(configuration(archive))

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(ProblemSolvingError, match="duplicate object members"),
    ):
        engine.propose(problem())


def test__propose__archives_http_error_response(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    error_body = b'{"error":"unsupported schema"}'
    http_error = urllib.error.HTTPError(
        url="http://127.0.0.1:11434/api/generate",
        code=400,
        msg="Bad Request",
        hdrs=Message(),
        fp=io.BytesIO(error_body),
    )
    responses = (*identity_responses(), http_error)
    engine = OllamaConversionIntentProposalEngine(configuration(archive))

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(ProblemSolvingError, match="HTTP 400; response sha256"),
    ):
        engine.propose(problem())

    response_path = next(archive.glob("*.response.json"))
    assert response_path.read_bytes() == error_body
    assert response_path.stat().st_mode & 0o777 == 0o600
    assert not tuple(archive.glob("*.receipt.json"))


def test__propose__rejects_proposal_over_byte_limit(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    generated = {
        "model": "test-model:1",
        "response": "x" * 257,
        "done": True,
    }
    responses = (*identity_responses(), response(generated))
    engine = OllamaConversionIntentProposalEngine(configuration(archive))

    with (
        patch("urllib.request.urlopen", side_effect=responses),
        pytest.raises(ProblemSolvingError, match="exceeds its byte limit"),
    ):
        engine.propose(problem())


@pytest.mark.parametrize(
    ("statement", "lexeme", "message"),
    [
        ("Convert 055.0 miles per hour.", "055.0", "lexeme is invalid"),
        ("Convert 55.0 miles per hour.", "54.0", "absent"),
        ("Convert 1000001 miles per hour.", "1000001", "outside bounds"),
        ("Convert 55.0\x00 miles per hour.", "55.0", "characters"),
    ],
)
def test__problem__rejects_invalid_admitted_input(
    statement: str,
    lexeme: str,
    message: str,
) -> None:
    with pytest.raises(ProblemSolvingError, match=message):
        ConversionIntentProblem(
            problem_id="synthetic.speed.invalid",
            statement=statement,
            statement_sha256=text_sha256(statement),
            candidate_given_lexeme=lexeme,
        )


def test__configuration__rejects_non_deterministic_sampling(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir(mode=0o700)
    values = configuration(archive).__dict__ | {"temperature": 0.1}

    with pytest.raises(ProblemSolvingError, match="must be deterministic"):
        OllamaConversionIntentConfiguration(**values)
