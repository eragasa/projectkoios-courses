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
Archive access is POSIX-only and fails closed unless the platform supports an
advisory `flock` on the already-open archive directory descriptor. Every
cooperating reader and writer takes that exclusive lock. Existing children are
read with a byte bound under the lock and are accepted only when the descriptor
and name identify the same regular, single-link file and its inode, device,
size, mode, ownership, modification time, and change time remain stable across
the read. A writer creates the final child directly, relative to the locked
directory descriptor, with `O_CREAT | O_EXCL | O_NOFOLLOW`; it writes and
syncs the open descriptor, then verifies stable descriptor/name identity,
metadata, link count, exact bytes, and digest before syncing the directory.

This is a cooperative protocol, not atomic publication to observers that ignore
the lock. The final name is visible as soon as it is created, initially with
mode 000 as an incomplete-state marker; only a fully verified record advances
to mode 0600. A write failure or process crash can therefore leave a partial,
unsynced, mismatched, or explicitly incomplete record. The library may restore
mode 000 through its retained descriptor after a handled failure, but it never
unlinks the name. A later retry rejects the marker or content mismatch, and an
operator must explicitly reconcile the residue. An abrupt crash during the
small final-mode transition and hostile metadata changes remain outside this
protocol's guarantees. Advisory locking and post-operation checks do not
constrain a hostile same-UID or other noncooperating process. Such a process can
replace, mutate, or link a child during or immediately after an operation. The
mode-0700 archive must therefore
be operated under a trusted, isolated UID, and every ordinary archive reader
and writer must use this protocol. The local HTTP transport ignores proxy
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
