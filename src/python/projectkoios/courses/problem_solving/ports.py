from __future__ import annotations

from typing import Protocol

from .models import (
    ComputationPlan,
    ComputationResult,
    EvidencePassage,
    ProblemMaterial,
    RetrievalRequest,
    SolutionDraft,
)


class EvidenceRetriever(Protocol):
    def retrieve(
        self,
        request: RetrievalRequest,
    ) -> tuple[EvidencePassage, ...]: ...


class SolutionProposalEngine(Protocol):
    def propose(
        self,
        problem: ProblemMaterial,
        evidence: tuple[EvidencePassage, ...],
    ) -> SolutionDraft: ...


class ComputationVerifier(Protocol):
    def verify(self, plan: ComputationPlan) -> ComputationResult: ...
