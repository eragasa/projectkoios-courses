"""Command-line entry points for course migration tools."""

from __future__ import annotations

import argparse
from pathlib import Path

from .sanitization import (
    CourseSanitizer,
    SanitizationError,
    SanitizationPolicy,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the course-sanitizer argument parser."""
    parser = argparse.ArgumentParser(
        description="Create a default-deny sanitized legacy course archive."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="atomically replace an existing destination after success",
    )
    parser.add_argument(
        "--ghostscript",
        default="gs",
        help="Ghostscript executable used to rewrite and inspect PDFs",
    )
    return parser


def main() -> int:
    """Run the course sanitizer."""
    arguments = build_parser().parse_args()
    try:
        policy = SanitizationPolicy.from_json(arguments.policy)
        result = CourseSanitizer(
            ghostscript=arguments.ghostscript
        ).sanitize(
            arguments.source,
            arguments.destination,
            policy,
            replace=arguments.replace,
        )
    except SanitizationError as error:
        print(f"error: {error}")
        return 2

    print(
        f"reviewed={result.reviewed} retained={result.retained} "
        f"excluded={result.excluded} redactions={result.redactions} "
        f"destination={result.destination}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
