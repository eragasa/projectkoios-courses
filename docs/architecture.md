# Course Domain Architecture

## Purpose

`projectkoios-courses` turns reusable knowledge and source material into
inspectable teaching sequences with explicit provenance.

## Ownership boundary

This repository owns:

- curriculum, course, module, lesson, and exercise domain objects
- learning objectives and prerequisite relationships
- pedagogical sequencing
- authoring, review, and validation workflow definitions
- mappings from course content to supporting notes and references

This repository does not own:

- retrieval and ranking (`projectkoios-search`)
- citation records (`projectkoios-references`)
- canonical atomic notes or vault mechanics
- generic LLM execution (`projectkoios-agent`)
- generic workflow execution (`projectkoios-workflow`)
- website rendering and publication (`projectkoios.com`)

## Intended flow

```text
notes + references + search results
    ↓
course-authoring application service
    ↓
curriculum → course → module → lesson → exercise
    ↓
review and provenance validation
    ↓
source course artifacts
    ↓
publishing adapter
```

Course content remains traceable to source objects. Publishing formats are
projections and must not become the canonical course model.

## Current package layout

```text
src/python/projectkoios/courses/
├── __init__.py
├── cli.py
└── sanitization.py
```

The initial implemented slice is a default-deny legacy-course sanitizer with course-specific allowlist policies, audit records, provenance output, and a command-line entry point. It creates candidates for manual review; it does not establish authorship, redistribution rights, privacy clearance, or publication approval.

Course-domain models and integration ports remain deferred until a focused design and test slice establishes their semantics.
