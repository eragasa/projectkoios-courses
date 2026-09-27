# Course Domain Architecture

## Purpose

`projectkoios-courses` turns reusable knowledge and source material into
inspectable teaching sequences with explicit provenance.

## Ownership boundary

This repository owns:

- curriculum, course, module, lesson, and exercise domain objects;
- learning objectives and prerequisite relationships;
- pedagogical sequencing;
- authoring, review, and validation workflow definitions;
- mappings from course content to supporting notes and references; and
- non-authoritative course-review matching semantics.

This repository does not own retrieval and ranking, citation records, canonical
atomic notes, generic LLM or workflow execution, HTTP APIs, browser views,
storage services, human decisions, or publication.

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

Course content remains traceable to source objects. Publishing formats and API
responses are projections and must not become the canonical course model.

## Current package layout

```text
src/python/projectkoios/courses/
├── __init__.py
├── cli.py
├── identities.py
├── review.py
├── sanitization.py
└── problem_solving/
    ├── archive.py
    ├── bounded_http.py
    ├── computation.py
    ├── conversion_intent.py
    ├── conversion_intent_cli.py
    ├── inventory.py
    ├── models.py
    ├── ports.py
    ├── proposal.py
    └── service.py
```

The implemented sanitization slice is default-deny. Course-specific allowlist
policies produce an audited candidate archive for manual review; they do not
establish authorship, redistribution rights, privacy clearance, acceptance, or
publication approval.

The implemented problem-solving slice inventories problem material, plans
purpose-scoped theory retrieval, produces unreviewed analytical candidates, and
optionally verifies restricted Python computations under an externally supplied
sandbox. Its Ollama adapters are loopback-only and proposal-only. They write
bounded request, response, and receipt files to a mode-0700 local archive.
Archive writes use direct-child containment checks, a stable directory identity,
no-follow opens, single-link checks, and collision-preserving publication.
Published children must have exactly one link after the temporary name is
removed. These checks reject pre-existing or observed outside hard links, but
they are not an isolation boundary against a malicious same-UID process that
can continuously mutate the archive namespace; the mode-0700 archive must be
operated under a trusted, isolated UID. The local HTTP transport ignores proxy
environment variables and rejects redirects. The general proposal adapter
defaults to a 131,072-byte HTTP response limit (configurable only from 1 to
1,048,576 bytes). Verifier stdout plus stderr share the documented
`maximum_output_bytes` operation bound, 65,536 bytes by default. Verifier
timeout or output overrun terminates the isolated process group. These are
deliberate local side effects; no adapter grants review or publication
authority.

## Read-only course-review contract

`review.py` is the canonical Courses-owned replacement for UI-local regular
expression matching. Its frozen inputs are:

- `CourseRecord`, the canonical course and institution identity plus display
  metadata;
- `CourseMaterial`, the canonical material-review state for that course; and
- `CourseProposalProjection`, a sanitized metadata-only teaching proposal.

`CourseReviewProjector` performs the prior course-code token comparison against
only `relative_path` and `suggested_group`. Matching is ASCII case-insensitive,
has explicit alphanumeric token boundaries, and is deterministic regardless of
input order. A proposal matching exactly one course becomes
`CourseMatchEvidence`. A proposal matching multiple courses is isolated in
`CourseMatchAmbiguity`; it is not silently attached to every course. A proposal
matching no course appears in `CourseReviewProjection.unmatched`.

A `CourseReviewCandidate` is emitted when a course already has
`review-candidate` material state or receives unique match evidence. Every
candidate has `candidate_evidence_only` authority and
`AUTOMATED_UNREVIEWED` status. The projector reads values and returns values: it
performs no file access, network request, persistence, acceptance, mutation, or
publication. API and Web repositories may adapt this contract but must not
reimplement or widen its semantics.
