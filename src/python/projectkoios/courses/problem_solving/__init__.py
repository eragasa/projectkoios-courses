from __future__ import annotations

from .computation import (
    DeterministicPythonComputationVerifier,
    PythonVerifierConfiguration,
)
from .conversion_intent import (
    ConversionAssumption,
    ConversionIntentProblem,
    ConversionIntentProposal,
    ConversionQuantityKind,
    ConversionUnit,
    OllamaConversionIntentConfiguration,
    OllamaConversionIntentProposalEngine,
)
from .inventory import (
    InventoryConfiguration,
    PageLine,
    ProblemCategory,
    ProblemInventory,
    ProblemInventoryBuilder,
    ProblemInventoryEntry,
)
from .models import (
    AnalyticalWork,
    ComputationPlan,
    ComputationResult,
    CorpusRole,
    EvidencePassage,
    ProblemMaterial,
    ProblemMode,
    ProblemSolvingError,
    RetrievalPurpose,
    RetrievalRequest,
    ReviewStatus,
    SolutionCandidate,
    SolutionDraft,
    SourceSpan,
    VerificationOutcome,
    text_sha256,
)
from .ports import (
    ComputationVerifier,
    EvidenceRetriever,
    SolutionProposalEngine,
)
from .proposal import (
    OllamaProposalConfiguration,
    OllamaSolutionProposalEngine,
)
from .service import ProblemSolverConfiguration, ProblemSolvingService

__all__ = [
    "AnalyticalWork",
    "ComputationPlan",
    "ComputationResult",
    "ComputationVerifier",
    "ConversionAssumption",
    "ConversionIntentProblem",
    "ConversionIntentProposal",
    "ConversionQuantityKind",
    "ConversionUnit",
    "CorpusRole",
    "DeterministicPythonComputationVerifier",
    "EvidencePassage",
    "EvidenceRetriever",
    "InventoryConfiguration",
    "OllamaConversionIntentConfiguration",
    "OllamaConversionIntentProposalEngine",
    "OllamaProposalConfiguration",
    "OllamaSolutionProposalEngine",
    "PageLine",
    "ProblemCategory",
    "ProblemInventory",
    "ProblemInventoryBuilder",
    "ProblemInventoryEntry",
    "ProblemMaterial",
    "ProblemMode",
    "ProblemSolverConfiguration",
    "ProblemSolvingError",
    "ProblemSolvingService",
    "PythonVerifierConfiguration",
    "RetrievalPurpose",
    "RetrievalRequest",
    "ReviewStatus",
    "SolutionCandidate",
    "SolutionDraft",
    "SolutionProposalEngine",
    "SourceSpan",
    "VerificationOutcome",
    "text_sha256",
]
