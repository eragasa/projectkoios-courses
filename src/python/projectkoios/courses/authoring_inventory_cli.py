"""CLI for deterministic read-only course source inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .authoring_inventory import (
    CourseAuthoringInventoryError,
    CourseAuthoringManifest,
    CourseSourceInventoryProjector,
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
    return parser


def main() -> int:
    """Load the manifest, inventory the root, and print deterministic JSON."""

    arguments = build_parser().parse_args()
    try:
        manifest = CourseAuthoringManifest.from_json(arguments.manifest)
        inventory = CourseSourceInventoryProjector().project(
            manifest=manifest,
            course_root=arguments.course_root,
        )
    except CourseAuthoringInventoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(inventory.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
