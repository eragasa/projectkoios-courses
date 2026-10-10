"""Course modeling and authoring for Project Koios."""

from __future__ import annotations

from .authoring_inventory import (
    CourseAuthoringInventoryError,
    CourseAuthoringManifest,
    CourseSourceInventory,
    CourseSourceInventoryProjector,
    ExternalBibliographyRoot,
    ExternalReferenceBinding,
)
from .reference_resolution import (
    CourseReferenceDisplayCitekeyStatus,
    CourseReferenceLocatorBinding,
    CourseReferenceResolutionError,
    CourseReferenceResolutionProjector,
    CourseReferenceResolutionScope,
    CourseReferenceResolutionWindow,
    CourseReferenceRole,
    CourseReferenceSelection,
    CourseReferenceSelectionOrigin,
)
from .review import (
    CourseCandidateReviewStatus,
    CourseDomainError,
    CourseMatchAmbiguity,
    CourseMatchEvidence,
    CourseMatchField,
    CourseMaterial,
    CourseMaterialsStatus,
    CourseProposalProjection,
    CourseRecord,
    CourseReviewAuthority,
    CourseReviewCandidate,
    CourseReviewProjection,
    CourseReviewProjector,
)

__all__ = [
    "CourseAuthoringInventoryError",
    "CourseAuthoringManifest",
    "CourseCandidateReviewStatus",
    "CourseDomainError",
    "CourseMatchAmbiguity",
    "CourseMatchEvidence",
    "CourseMatchField",
    "CourseMaterial",
    "CourseMaterialsStatus",
    "CourseProposalProjection",
    "CourseReferenceDisplayCitekeyStatus",
    "CourseReferenceLocatorBinding",
    "CourseReferenceResolutionError",
    "CourseReferenceResolutionProjector",
    "CourseReferenceResolutionScope",
    "CourseReferenceResolutionWindow",
    "CourseReferenceRole",
    "CourseReferenceSelection",
    "CourseReferenceSelectionOrigin",
    "CourseRecord",
    "CourseReviewAuthority",
    "CourseReviewCandidate",
    "CourseReviewProjection",
    "CourseReviewProjector",
    "CourseSourceInventory",
    "CourseSourceInventoryProjector",
    "ExternalBibliographyRoot",
    "ExternalReferenceBinding",
]
