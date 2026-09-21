# Problem-solving retrieval boundary

Status: implemented candidate; not an accepted public contract.

Course problem solving always emits a request with the explicit
`problem_solving` purpose. Its only admitted content role is
`theory_evidence`, and each returned passage must independently declare
`problem_solving` admission. A caller cannot widen admission by attaching an
arbitrary role allowlist or by assigning a purpose admission to a forbidden
role.

The service independently validates every returned passage and rejects:

- source worked examples;
- source solutions;
- problem statements and exercises;
- generated solution candidates; and
- reviewed solutions.

Reviewed solutions may be admitted by a separate lecture-authoring policy, but
that admission does not authorize their use as solving evidence. A generated or
reviewed solution can therefore never become evidence for solving itself.

Content classification remains a producer responsibility. A page containing
both theory and excluded course material is not automatically theory evidence.
Until a finer source-boundary projection is validated, a producer should assign
the most restrictive applicable role and accept the resulting loss of recall.
Insufficient admitted evidence must be surfaced rather than recovered through a
cross-purpose fallback.
