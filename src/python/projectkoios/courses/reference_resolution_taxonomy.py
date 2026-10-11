"""Nominal class roles for course reference-resolution boundaries."""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__: tuple[str, ...] = (
    "CourseReferenceActionRequest",
    "CourseReferenceActionResult",
    "CourseReferenceActionizer",
    "CourseReferenceDataObject",
)


class CourseReferenceDataObject(ABC):
    """Identify one immutable represented reference-resolution value."""

    __slots__ = ()

    @abstractmethod
    def __init__(self) -> None:
        """Initialize one complete represented value."""


class CourseReferenceActionRequest(CourseReferenceDataObject, ABC):
    """Identify the immutable input to one reference-resolution action."""

    __slots__ = ()


class CourseReferenceActionResult(CourseReferenceDataObject, ABC):
    """Identify the immutable output of one reference-resolution action."""

    __slots__ = ()


class CourseReferenceActionizer[
    RequestT: CourseReferenceActionRequest,
    ResultT: CourseReferenceActionResult,
](ABC):
    """Define one function-like course reference-resolution action."""

    __slots__ = ()

    @abstractmethod
    def action(self, *, request: RequestT) -> ResultT:
        """Return the result produced from one complete request."""
