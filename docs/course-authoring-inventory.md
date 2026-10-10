# Course authoring source inventory

## Scope

The first course-authoring slice is a deterministic, read-only inventory. It
loads a tracked version-1 JSON manifest and inspects an explicitly supplied
private course root. The root is a runtime capability: its absolute path is
never retained in the result.

This slice does not edit a manuscript, import or export bibliography bytes,
retrieve evidence, execute a workflow, build LaTeX, run notebooks, accept a
reference identity, or publish anything.

## Command

```bash
koios-course-inventory \
  manifests/dlsu.phy627m.gs1.authoring.json \
  --course-root /explicit/private/course/root
```

The command writes the inventory JSON to standard output and performs no source
writes. A caller may redirect output to an operator-selected location. The
projection contains only the manifest's root alias and source-root-relative
POSIX paths.

## Manifest boundary

A manifest records:

- course identity, title, and non-path root alias;
- the root-relative main TeX file and notebook directories;
- existing inline citekeys that must be preserved during later bibliography
  migration; and
- exact References-owned source IDs and revisions plus their selected candidate
  citekeys and explicit identity statuses.

The Solid State II manifest binds two distinct References sources:
`catalog-collection:solidstate` and
`catalog-collection:semiconductorphysics`. Their nine keys remain
`unaccepted-candidate` and `proposed-noncanonical`. The binding makes those
private-drafting candidates visible to Courses; it does not canonicalize them,
copy bibliographic metadata, accept manuscript use, clear rights, or authorize
publication.

Together with the five preserved inline keys, the declared draft bibliography
scope contains fourteen works. References still owns bibliography bytes,
identity decisions, and any later canonical citekey migration.

## Inventory projection

The projector follows literal `\\input` and `\\include` commands from the main
file, rejects escapes and symlinks, and records exact byte length and SHA-256
for every visited TeX source. It also records:

- literal include edges;
- parts, chapters, sections, and subsections;
- citation occurrences and inline `\\bibitem` keys;
- declared bibliography files, when present;
- notebook byte identities; and
- mechanical gaps between the observed inline/cited keys and the manifest's
  planned reference scope.

The inventory and manifest identities are deterministic SHA-256 content
identities. `reference_scope.status=complete` means only that the expected
inline keys are present, no unplanned inline keys were observed, and every
cited key is in the observed inline or externally bound set. It is not a
reference, scientific, pedagogical, rights, or publication decision.

The initial scanner deliberately supports only literal root-relative LaTeX
paths and the command forms needed by the current manuscript. Dynamic TeX
macros and a general TeX build graph are outside this slice and fail through
missing-file or incomplete-inventory evidence rather than a compatibility
layer.
