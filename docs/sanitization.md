# Legacy Course Sanitization

## Policy

Course sanitization is default-deny. A file is retained only when a reviewed,
course-specific policy explicitly allowlists it.

The sanitizer is intended to produce a candidate archive for manual review. It
does not establish authorship, permission, copyright status, or publication
approval.

## Retention rules

A conservative, potentially publishable archive may retain:

- student-authored text and source code
- bibliographic metadata
- public course records when their visible content passes privacy review
- PDFs that can be rewritten to clear document metadata and whose extracted
  visible text contains no configured personal identifiers

Everything else remains excluded from version control by default. The audit
retains its relative filename, checksum, byte size, and classification reason.
A course policy may set `copy_excluded` to keep byte-identical local reference
copies under `legacy/private-sources/`; that directory must be explicitly
ignored by the destination repository and must never be force-added.

Intellectual sources can be declared in the policy's `provenance` list. The
sanitizer renders those records as BibLaTeX in `legacy/provenance.bib`, including
the local source path and SHA-256 digest.

## Required exclusions

Do not retain:

- grades, transcripts, student IDs, rosters, or other student submissions
- private instructor feedback
- credentials or secrets
- textbooks, solution manuals, or proprietary course packs
- binaries or archives whose contents and rights have not been reviewed
- generated build products when reviewed source is available

## Command

```bash
koios-course-sanitize SOURCE DESTINATION \
  --policy policies/pacific_ENGR219.json
```

Use `--replace` only to atomically replace a previously generated destination.
The old destination remains intact if sanitization fails before installation.

## Output

```text
legacy/
├── README.md
├── MANIFEST.sha256
├── audit/
│   ├── classification.csv
│   ├── private-sources.sha256  # when private copies are enabled
│   └── sanitization-report.json
├── course-records/
├── private-sources/            # local and Git-ignored
├── provenance.bib              # when provenance entries are configured
└── student-work/
```

The output records a source alias, never an absolute home path. Literal
redaction values are not written to the generated report; only their labels and
redaction counts are recorded.

## Review gate

Before committing or publishing:

1. verify `MANIFEST.sha256`
2. review every retained file
3. inspect the classification report for incorrect ownership assumptions
4. confirm `private-sources/` is ignored and no excluded file is staged
5. review `provenance.bib` and the private-source checksum inventory
6. confirm repository visibility and licensing separately
