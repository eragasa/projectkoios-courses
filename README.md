# projectkoios-courses

Course modeling and evidence-backed authoring for Project Koios.

This repository owns:

- course, curriculum, module, lesson, and exercise semantics
- pedagogical sequencing and prerequisite relationships
- course-authoring workflows and validation rules
- source-to-course provenance
- source forms of generated course artifacts

It consumes search, references, notes, agent execution, and generic workflow
services through explicit interfaces. It does not own those services or the
publishing UI.

The initial Python namespace is `projectkoios.courses`.

## Legacy sanitization

The default-deny legacy sanitizer retains only files explicitly allowlisted by
a reviewed course policy:

```bash
koios-course-sanitize SOURCE DESTINATION \
  --policy policies/pacific_ENGR219.json
```

See `docs/sanitization.md` for the privacy, ownership, audit, and manual-review
requirements.

Repository routing is documented in
`projectkoios-bootstrap/maps/repositories.md`.
