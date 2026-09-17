# AGENTS.md — projectkoios-courses

## Ownership

This repository owns the course domain for Project Koios: curriculum and course
models, pedagogical sequencing, course-authoring workflows, validation rules,
and source-to-course provenance.

It does not own generic search, references, notes, LLM execution, workflow
execution, or publishing infrastructure. Those belong to their respective
Project Koios repositories.

Repository routing is documented in
`projectkoios-bootstrap/maps/repositories.md`.

## Setup

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Commands

| Action | Command |
|---|---|
| Run tests | `pytest` |
| Lint | `ruff check .` |
| Typecheck | `mypy src/python` |

## Package rules

- Source root is `src/python/projectkoios/courses/`.
- Use `from __future__ import annotations` in every Python module.
- Use frozen dataclasses for internal domain objects.
- Use Pydantic only at external boundaries.
- Keep integrations behind explicit protocols or adapters.
- Production code must not import from `dev/`.
- Ruff uses line length 80, double quotes, and Python 3.14.
