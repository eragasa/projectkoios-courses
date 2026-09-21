from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .models import (
    ComputationPlan,
    EvidencePassage,
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    RetrievalPurpose,
    RetrievalRequest,
    ReviewStatus,
    SolutionCandidate,
    SolutionDraft,
    VerificationOutcome,
)
from .ports import (
    ComputationVerifier,
    EvidenceRetriever,
    SolutionProposalEngine,
)


@dataclass(frozen=True)
class ProblemSolverConfiguration:
    retrieval_limit: int
    minimum_evidence_passages: int
    require_substitution_for_analytical: bool
    require_computational_agreement: bool
    allowed_runtime_identities: tuple[str, ...]
    maximum_computation_timeout_seconds: int

    def __post_init__(self) -> None:
        if self.retrieval_limit < 1:
            raise ProblemSolvingError("retrieval limit must be positive")
        if not 1 <= self.minimum_evidence_passages <= self.retrieval_limit:
            raise ProblemSolvingError("minimum evidence count is out of range")
        if not self.allowed_runtime_identities:
            raise ProblemSolvingError(
                "at least one computation runtime is required"
            )
        if self.maximum_computation_timeout_seconds < 1:
            raise ProblemSolvingError("maximum computation timeout is invalid")


class ProblemSolvingService:
    def __init__(
        self,
        *,
        retriever: EvidenceRetriever,
        proposal_engine: SolutionProposalEngine,
        computation_verifier: ComputationVerifier,
        configuration: ProblemSolverConfiguration,
    ) -> None:
        self._retriever = retriever
        self._proposal_engine = proposal_engine
        self._computation_verifier = computation_verifier
        self._configuration = configuration

    def plan_retrieval(self, problem: ProblemMaterial) -> RetrievalRequest:
        return RetrievalRequest(
            query=problem.statement,
            problem_id=problem.problem_id,
            purpose=RetrievalPurpose.PROBLEM_SOLVING,
            excluded_passage_ids=(problem.problem_id,),
            limit=self._configuration.retrieval_limit,
        )

    def solve(self, problem: ProblemMaterial) -> SolutionCandidate:
        request = self.plan_retrieval(problem)
        evidence = self._retriever.retrieve(request)
        self._validate_evidence(evidence, request)

        draft = self._proposal_engine.propose(problem, evidence)
        self._validate_draft(problem, evidence, draft)

        computation_result = None
        findings: list[str] = []
        if problem.mode is ProblemMode.ANALYTICAL_COMPUTATIONAL:
            plan = draft.computation_plan
            if plan is None:
                raise ProblemSolvingError(
                    "analytical-computational problem requires a "
                    "computation plan"
                )
            self._validate_computation_plan(plan)
            computation_result = self._computation_verifier.verify(plan)
            if computation_result.plan_identity != plan.identity:
                raise ProblemSolvingError(
                    "computation result does not match the proposed plan"
                )
            if computation_result.outcome is not VerificationOutcome.PASSED:
                findings.append(
                    "computational verification did not pass: "
                    f"{computation_result.outcome}"
                )
            if (
                self._configuration.require_computational_agreement
                and computation_result.analytical_agreement is not True
            ):
                findings.append(
                    "analytical and computational results are not verified "
                    "to agree"
                )
            findings.extend(computation_result.findings)

        candidate_id = self._candidate_identity(
            problem,
            draft,
            computation_result_identity=(
                computation_result.output_sha256
                if computation_result is not None
                else None
            ),
        )
        cited = set(draft.evidence_passage_ids)
        cited_evidence = tuple(
            passage for passage in evidence if passage.passage_id in cited
        )
        return SolutionCandidate(
            candidate_id=candidate_id,
            problem=problem,
            evidence=cited_evidence,
            analytical_work=draft.analytical_work,
            computation_result=computation_result,
            review_status=ReviewStatus.AUTOMATED_UNREVIEWED,
            findings=tuple(findings),
        )

    def _validate_evidence(
        self,
        evidence: tuple[EvidencePassage, ...],
        request: RetrievalRequest,
    ) -> None:
        if len(evidence) < self._configuration.minimum_evidence_passages:
            raise ProblemSolvingError(
                "retrieval returned insufficient evidence"
            )
        if len(evidence) > request.limit:
            raise ProblemSolvingError("retrieval exceeded its configured limit")
        passage_ids: list[str] = []
        for item in evidence:
            if item.corpus_role not in request.admitted_roles:
                raise ProblemSolvingError(
                    "retrieval returned material not admitted for "
                    "problem solving"
                )
            if request.purpose not in item.admitted_purposes:
                raise ProblemSolvingError(
                    "retrieval returned evidence without problem-solving "
                    "admission"
                )
            passage_ids.append(item.passage_id)
        if len(set(passage_ids)) != len(passage_ids):
            raise ProblemSolvingError("retrieval returned duplicate evidence")
        if set(passage_ids) & set(request.excluded_passage_ids):
            raise ProblemSolvingError("retrieval returned excluded material")

    def _validate_draft(
        self,
        problem: ProblemMaterial,
        evidence: tuple[EvidencePassage, ...],
        draft: SolutionDraft,
    ) -> None:
        if draft.problem_id != problem.problem_id:
            raise ProblemSolvingError("proposal targets a different problem")
        available_ids = {passage.passage_id for passage in evidence}
        cited_ids = draft.evidence_passage_ids
        if not cited_ids or len(set(cited_ids)) != len(cited_ids):
            raise ProblemSolvingError(
                "proposal must cite unique retrieved evidence passages"
            )
        if not set(cited_ids) <= available_ids:
            raise ProblemSolvingError("proposal cites unavailable evidence")

        work = draft.analytical_work
        if problem.mode is not ProblemMode.CONCEPTUAL:
            if not work.givens:
                raise ProblemSolvingError("analytical solution requires givens")
            if not work.governing_equations:
                raise ProblemSolvingError(
                    "analytical solution requires governing equations"
                )
            if not work.derivation_steps or work.symbolic_result is None:
                raise ProblemSolvingError(
                    "analytical solution requires a symbolic derivation"
                )
            if (
                self._configuration.require_substitution_for_analytical
                and not work.substitutions
            ):
                raise ProblemSolvingError(
                    "analytical solution requires value substitution"
                )
        if problem.mode is ProblemMode.ANALYTICAL_COMPUTATIONAL:
            if draft.computation_plan is None:
                raise ProblemSolvingError("computation plan is required")
        elif draft.computation_plan is not None:
            raise ProblemSolvingError(
                "computation plan is not allowed for this problem mode"
            )

    def _validate_computation_plan(self, plan: ComputationPlan) -> None:
        if (
            plan.runtime_identity
            not in self._configuration.allowed_runtime_identities
        ):
            raise ProblemSolvingError("computation runtime is not authorized")
        if (
            plan.timeout_seconds
            > self._configuration.maximum_computation_timeout_seconds
        ):
            raise ProblemSolvingError("computation timeout exceeds its bound")

    @staticmethod
    def _candidate_identity(
        problem: ProblemMaterial,
        draft: SolutionDraft,
        *,
        computation_result_identity: str | None,
    ) -> str:
        payload = {
            "analytical_work": asdict(draft.analytical_work),
            "computation_plan_identity": (
                draft.computation_plan.identity
                if draft.computation_plan is not None
                else None
            ),
            "computation_result_identity": computation_result_identity,
            "evidence_passage_ids": draft.evidence_passage_ids,
            "problem_id": problem.problem_id,
            "statement_sha256": problem.statement_sha256,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return f"solution-candidate:sha256:{digest}"
