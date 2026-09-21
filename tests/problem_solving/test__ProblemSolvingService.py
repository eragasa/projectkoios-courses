from __future__ import annotations

from dataclasses import replace

import pytest
from projectkoios.courses.problem_solving import (
    AnalyticalWork,
    ComputationPlan,
    ComputationResult,
    CorpusRole,
    EvidencePassage,
    ProblemMaterial,
    ProblemMode,
    ProblemSolverConfiguration,
    ProblemSolvingError,
    ProblemSolvingService,
    RetrievalPurpose,
    ReviewStatus,
    SolutionDraft,
    SourceSpan,
    VerificationOutcome,
    text_sha256,
)

SOURCE = (
    "source:sha256:"
    "0bc11b67facf00abf7c8f370adb99f7acdcb0348c7108774e80a2b1c7a398687"
)
RUNTIME = "python:3.14+sympy:1.14"


def source(page: int) -> SourceSpan:
    return SourceSpan(SOURCE, page, page)


def problem(
    mode: ProblemMode = ProblemMode.ANALYTICAL,
) -> ProblemMaterial:
    statement = "Determine the entropy change for the stated process."
    return ProblemMaterial(
        problem_id="YF13Ed.20.27",
        statement=statement,
        statement_sha256=text_sha256(statement),
        mode=mode,
        source=source(708),
    )


def evidence(
    role: CorpusRole = CorpusRole.THEORY_EVIDENCE,
    admitted_purposes: tuple[RetrievalPurpose, ...] = (
        RetrievalPurpose.PROBLEM_SOLVING,
    ),
) -> tuple[EvidencePassage, ...]:
    return (
        EvidencePassage(
            passage_id="passage:entropy-definition",
            text="Entropy is evaluated along a reversible path.",
            source=SourceSpan(SOURCE, 695, 700),
            corpus_role=role,
            admitted_purposes=admitted_purposes,
        ),
        EvidencePassage(
            passage_id="passage:isothermal-ideal-gas",
            text="For an isothermal ideal gas, integrate nR dV/V.",
            source=SourceSpan(SOURCE, 697, 698),
            corpus_role=CorpusRole.THEORY_EVIDENCE,
            admitted_purposes=admitted_purposes,
        ),
    )


def analytical_work() -> AnalyticalWork:
    return AnalyticalWork(
        givens=("n", "V_i", "V_f"),
        assumptions=("ideal gas", "reversible isothermal path"),
        governing_equations=(r"\mathrm{d}S=\mathrm{d}Q_{rev}/T",),
        derivation_steps=(r"\Delta S=nR\ln(V_f/V_i)",),
        symbolic_result=r"\Delta S=nR\ln(V_f/V_i)",
        substitutions=(r"n=3.00\ \mathrm{mol}",),
        conclusion="The entropy change follows from the volume ratio.",
    )


def computation_plan() -> ComputationPlan:
    return ComputationPlan(
        language="python",
        source_code="from math import log\nprint(3 * 8.314 * log(2))\n",
        runtime_identity=RUNTIME,
        dependency_lock_identity="lock:sha256:" + "b" * 64,
        seed=20260921,
        timeout_seconds=10,
        network_access=False,
    )


class Retriever:
    def __init__(self, passages: tuple[EvidencePassage, ...]) -> None:
        self.passages = passages
        self.request = None

    def retrieve(self, request):  # type: ignore[no-untyped-def]
        self.request = request
        return self.passages


class ProposalEngine:
    def __init__(self, draft: SolutionDraft) -> None:
        self.draft = draft

    def propose(self, problem, evidence):  # type: ignore[no-untyped-def]
        return self.draft


class Verifier:
    def __init__(self, result: ComputationResult | None = None) -> None:
        self.result = result
        self.plan = None

    def verify(self, plan):  # type: ignore[no-untyped-def]
        self.plan = plan
        if self.result is not None:
            return self.result
        return ComputationResult(
            plan_identity=plan.identity,
            outcome=VerificationOutcome.PASSED,
            result_summary="The numerical evaluation agrees.",
            output_sha256="a" * 64,
            analytical_agreement=True,
            findings=(),
        )


def configuration() -> ProblemSolverConfiguration:
    return ProblemSolverConfiguration(
        retrieval_limit=6,
        minimum_evidence_passages=2,
        require_substitution_for_analytical=True,
        require_computational_agreement=True,
        allowed_runtime_identities=(RUNTIME,),
        maximum_computation_timeout_seconds=30,
    )


def service(
    mode: ProblemMode = ProblemMode.ANALYTICAL,
    passages: tuple[EvidencePassage, ...] | None = None,
    result: ComputationResult | None = None,
) -> tuple[ProblemSolvingService, Retriever, Verifier]:
    selected_evidence = evidence() if passages is None else passages
    selected_plan = (
        computation_plan()
        if mode is ProblemMode.ANALYTICAL_COMPUTATIONAL
        else None
    )
    draft = SolutionDraft(
        problem_id="YF13Ed.20.27",
        evidence_passage_ids=tuple(
            item.passage_id for item in selected_evidence
        ),
        analytical_work=analytical_work(),
        computation_plan=selected_plan,
    )
    retriever = Retriever(selected_evidence)
    verifier = Verifier(result)
    return (
        ProblemSolvingService(
            retriever=retriever,
            proposal_engine=ProposalEngine(draft),
            computation_verifier=verifier,
            configuration=configuration(),
        ),
        retriever,
        verifier,
    )


def test__plan_retrieval__allows_only_authoritative_evidence() -> None:
    solver, _, _ = service()

    request = solver.plan_retrieval(problem())

    assert request.purpose is RetrievalPurpose.PROBLEM_SOLVING
    assert request.admitted_roles == (CorpusRole.THEORY_EVIDENCE,)
    assert request.excluded_passage_ids == ("YF13Ed.20.27",)
    assert request.query == problem().statement


def test__retrieval_request__rejects_non_solving_purpose() -> None:
    solver, _, _ = service()
    request = solver.plan_retrieval(problem())

    with pytest.raises(
        ProblemSolvingError,
        match="problem-solving retrieval purpose",
    ):
        replace(request, purpose=RetrievalPurpose.LECTURE_AUTHORING)


def test__solve__returns_unreviewed_analytical_candidate() -> None:
    solver, retriever, verifier = service()

    candidate = solver.solve(problem())

    assert candidate.review_status is ReviewStatus.AUTOMATED_UNREVIEWED
    assert len(candidate.evidence) == 2
    assert candidate.computation_result is None
    assert candidate.findings == ()
    assert retriever.request is not None
    assert verifier.plan is None


@pytest.mark.parametrize(
    "role",
    (
        CorpusRole.SOURCE_WORKED_EXAMPLE,
        CorpusRole.SOURCE_SOLUTION,
        CorpusRole.PROBLEM_MATERIAL,
        CorpusRole.GENERATED_SOLUTION,
        CorpusRole.REVIEWED_SOLUTION,
    ),
)
def test__solve__rejects_material_not_admitted_for_problem_solving(
    role: CorpusRole,
) -> None:
    contaminated = evidence(role)
    solver, _, _ = service(passages=contaminated)

    with pytest.raises(
        ProblemSolvingError,
        match="not admitted for problem solving",
    ):
        solver.solve(problem())


def test__solve__rejects_theory_without_problem_solving_admission() -> None:
    solver, _, _ = service(passages=evidence(admitted_purposes=()))

    with pytest.raises(
        ProblemSolvingError,
        match="without problem-solving admission",
    ):
        solver.solve(problem())


def test__solve__requires_citations_from_retrieved_evidence() -> None:
    solver, retriever, verifier = service()
    invalid_draft = SolutionDraft(
        problem_id="YF13Ed.20.27",
        evidence_passage_ids=("passage:not-retrieved",),
        analytical_work=analytical_work(),
        computation_plan=None,
    )
    solver = ProblemSolvingService(
        retriever=retriever,
        proposal_engine=ProposalEngine(invalid_draft),
        computation_verifier=verifier,
        configuration=configuration(),
    )

    with pytest.raises(ProblemSolvingError, match="unavailable evidence"):
        solver.solve(problem())


def test__solve__verifies_computational_plan() -> None:
    mode = ProblemMode.ANALYTICAL_COMPUTATIONAL
    solver, _, verifier = service(mode)

    candidate = solver.solve(problem(mode))

    assert verifier.plan is not None
    assert candidate.computation_result is not None
    assert candidate.computation_result.outcome is VerificationOutcome.PASSED
    assert candidate.findings == ()


def test__solve__retains_failed_computation_as_a_finding() -> None:
    mode = ProblemMode.ANALYTICAL_COMPUTATIONAL
    plan = computation_plan()
    failed = ComputationResult(
        plan_identity=plan.identity,
        outcome=VerificationOutcome.FAILED,
        result_summary="The numerical result disagrees.",
        output_sha256="c" * 64,
        analytical_agreement=False,
        findings=("relative error exceeds tolerance",),
    )
    solver, _, _ = service(mode, result=failed)

    candidate = solver.solve(problem(mode))

    assert candidate.review_status is ReviewStatus.AUTOMATED_UNREVIEWED
    assert len(candidate.findings) == 3
    assert "relative error exceeds tolerance" in candidate.findings


def test__solve__candidate_identity_is_reproducible() -> None:
    first, _, _ = service()
    second, _, _ = service()

    first_candidate = first.solve(problem())
    second_candidate = second.solve(problem())

    assert first_candidate.candidate_id == second_candidate.candidate_id


def test__problem_material__rejects_statement_identity_mismatch() -> None:
    with pytest.raises(ProblemSolvingError, match="identity mismatch"):
        replace(problem(), statement_sha256="0" * 64)


def test__computation_plan__rejects_network_access() -> None:
    with pytest.raises(ProblemSolvingError, match="cannot use the network"):
        replace(computation_plan(), network_access=True)
