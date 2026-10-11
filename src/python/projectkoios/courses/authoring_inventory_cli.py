"""CLI for deterministic read-only course source inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .authoring_inventory import (
    CourseAuthoringInventoryError,
    CourseAuthoringManifest,
    CourseSourceInventoryProjector,
    ExternalBibliographyRoot,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the course-inventory argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Inspect one explicit course root without writing to it or "
            "serializing its absolute path."
        )
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--course-root",
        required=True,
        type=Path,
        help="explicit private source root; never retained in output",
    )
    parser.add_argument(
        "--bibliography-root",
        action="append",
        default=[],
        metavar="ALIAS=PATH",
        help=(
            "named private root for an external bibliography; repeatable and "
            "never retained as an absolute path"
        ),
    )
    return parser


def _bibliography_roots(
    values: list[str],
) -> tuple[ExternalBibliographyRoot, ...]:
    """Parse explicit named bibliography roots.

    Parameters
    ----------
    values
        Repeated ``ALIAS=PATH`` command-line values.

    Returns
    -------
    tuple[ExternalBibliographyRoot, ...]
        Immutable registrations in command-line order.

    Raises
    ------
    CourseAuthoringInventoryError
        If a value omits a nonempty alias or path.
    """

    roots: list[ExternalBibliographyRoot] = []
    for value in values:
        alias, separator, raw_path = value.partition("=")
        if not separator or not alias or not raw_path:
            raise CourseAuthoringInventoryError(
                "bibliography roots must use ALIAS=PATH"
            )
        roots.append(ExternalBibliographyRoot(alias=alias, path=Path(raw_path)))
    return tuple(roots)


def main() -> int:
    """Load the manifest, inventory the root, and print deterministic JSON."""

    arguments = build_parser().parse_args()
    try:
        manifest = CourseAuthoringManifest.from_json(arguments.manifest)
        inventory = CourseSourceInventoryProjector().project(
            manifest=manifest,
            course_root=arguments.course_root,
            external_bibliography_roots=_bibliography_roots(
                arguments.bibliography_root
            ),
        )
    except CourseAuthoringInventoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(inventory.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
