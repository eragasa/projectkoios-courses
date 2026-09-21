from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class ProblemSolvingError(ValueError):
    """Raised when problem-solving domain invariants are violated."""


class CorpusRole(StrEnum):
    EVIDENCE = "evidence"
    PROBLEM_MATERIAL = "problem_material"
    GENERATED_SOLUTION = "generated_solution"
    REVIEWED_SOLUTION = "reviewed_solution"


class ProblemMode(StrEnum):
    CONCEPTUAL = "conceptual"
    ANALYTICAL = "analytical"
    ANALYTICAL_COMPUTATIONAL = "analytical_computational"


class ReviewStatus(StrEnum):
    AUTOMATED_UNREVIEWED = "AUTOMATED_UNREVIEWED"
    HUMAN_ACCEPTED = "HUMAN_ACCEPTED"
    HUMAN_REJECTED = "HUMAN_REJECTED"


class VerificationOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class SourceSpan:
    source_identity: str
    physical_page_start: int
    physical_page_end: int

    def __post_init__(self) -> None:
        if not self.source_identity.startswith("source:sha256:"):
            raise ProblemSolvingError("source identity must use source:sha256")
        if self.physical_page_start < 1:
            raise ProblemSolvingError("physical page start must be positive")
        if self.physical_page_end < self.physical_page_start:
            raise ProblemSolvingError("source page span is reversed")


@dataclass(frozen=True)
class ProblemMaterial:
    problem_id: str
    statement: str
    statement_sha256: str
    mode: ProblemMode
    source: SourceSpan
    figure_ids: tuple[str, ...] = ()
    corpus_role: CorpusRole = CorpusRole.PROBLEM_MATERIAL

    def __post_init__(self) -> None:
        if not self.problem_id or not self.statement.strip():
            raise ProblemSolvingError(
                "problem identity and statement are required"
            )
        if self.statement_sha256 != text_sha256(self.statement):
            raise ProblemSolvingError("problem statement identity mismatch")
        if self.corpus_role is not CorpusRole.PROBLEM_MATERIAL:
            raise ProblemSolvingError("problem must use problem_material role")
        if len(set(self.figure_ids)) != len(self.figure_ids):
            raise ProblemSolvingError(
                "problem figure identities must be unique"
            )


@dataclass(frozen=True)
class EvidencePassage:
    passage_id: str
    text: str
    source: SourceSpan
    corpus_role: CorpusRole

    def __post_init__(self) -> None:
        if not self.passage_id or not self.text.strip():
            raise ProblemSolvingError("evidence identity and text are required")


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    problem_id: str
    allowed_roles: tuple[CorpusRole, ...]
    excluded_passage_ids: tuple[str, ...]
    limit: int

    def __post_init__(self) -> None:
        if not self.query.strip() or not self.problem_id:
            raise ProblemSolvingError(
                "retrieval query and problem id are required"
            )
        if self.allowed_roles != (CorpusRole.EVIDENCE,):
            raise ProblemSolvingError("retrieval must be evidence-only")
        if self.limit < 1:
            raise ProblemSolvingError("retrieval limit must be positive")


@dataclass(frozen=True)
class AnalyticalWork:
    givens: tuple[str, ...]
    assumptions: tuple[str, ...]
    governing_equations: tuple[str, ...]
    derivation_steps: tuple[str, ...]
    symbolic_result: str | None
    substitutions: tuple[str, ...]
    conclusion: str

    def __post_init__(self) -> None:
        if not self.conclusion.strip():
            raise ProblemSolvingError("analytical conclusion is required")


@dataclass(frozen=True)
class ComputationPlan:
    language: str
    source_code: str
    runtime_identity: str
    dependency_lock_identity: str
    seed: int
    timeout_seconds: int
    network_access: bool

    def __post_init__(self) -> None:
        if self.language != "python":
            raise ProblemSolvingError(
                "only configured Python computation is allowed"
            )
        if not self.source_code.strip():
            raise ProblemSolvingError("computation source code is required")
        if not self.runtime_identity or not self.dependency_lock_identity:
            raise ProblemSolvingError(
                "computation environment identities are required"
            )
        if self.seed < 0 or self.timeout_seconds < 1:
            raise ProblemSolvingError("invalid computation seed or timeout")
        if self.network_access:
            raise ProblemSolvingError(
                "problem computation cannot use the network"
            )

    @property
    def identity(self) -> str:
        payload = {
            "dependency_lock_identity": self.dependency_lock_identity,
            "language": self.language,
            "network_access": self.network_access,
            "runtime_identity": self.runtime_identity,
            "seed": self.seed,
            "source_code": self.source_code,
            "timeout_seconds": self.timeout_seconds,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"computation-plan:sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class ComputationResult:
    plan_identity: str
    outcome: VerificationOutcome
    result_summary: str
    output_sha256: str
    analytical_agreement: bool | None
    findings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.plan_identity.startswith("computation-plan:sha256:"):
            raise ProblemSolvingError(
                "computation result has invalid plan identity"
            )
        if not self.result_summary.strip() or len(self.output_sha256) != 64:
            raise ProblemSolvingError("computation result is incomplete")


@dataclass(frozen=True)
class SolutionDraft:
    problem_id: str
    evidence_passage_ids: tuple[str, ...]
    analytical_work: AnalyticalWork
    computation_plan: ComputationPlan | None


@dataclass(frozen=True)
class SolutionCandidate:
    candidate_id: str
    problem: ProblemMaterial
    evidence: tuple[EvidencePassage, ...]
    analytical_work: AnalyticalWork
    computation_result: ComputationResult | None
    review_status: ReviewStatus
    findings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.review_status is not ReviewStatus.AUTOMATED_UNREVIEWED:
            raise ProblemSolvingError(
                "new solution candidates must remain AUTOMATED_UNREVIEWED"
            )


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
