# Course reference-resolution scope

## Purpose

`CourseReferenceResolutionScope` is the immutable Courses-owned queue seed for
resolving the references selected by one course. It binds the current course
authoring manifest and complete source inventory to an exact one-to-one ordered
tuple of opaque locators supplied by References-owned composition.

The projection owns course context, inclusion reason, role, order, and
navigation. It does not own bibliographic metadata, reference identity,
citekey acceptance, document binding, source assets, resolution decisions, or
allowed mutation operations.

## Explicit inputs

`CourseReferenceResolutionProjector.project` requires all three inputs:

1. a fresh `CourseAuthoringManifest`;
2. a fresh `CourseSourceInventory` derived from exactly that manifest, with
   `reference_scope.status=complete`; and
3. an immutable tuple of `CourseReferenceLocatorBinding` values in the exact
   manifest selection order.

Every locator binding contains a one-based ordinal, the expected course display
citekey and its observed/proposed status, and one bounded opaque
References-owned locator. Courses validates the locator as an opaque identity;
it neither parses a References schema nor imports the References package.
Ordinal and full-sequence equality make citekeys consistency guards rather than
join keys.

The tuple must cover every selected work exactly once. Missing, extra,
duplicate, reordered, over-limit, or malformed locators fail closed. Stale
manifest and inventory identities, a manifest/inventory mismatch, and an
incomplete inventory also fail closed.

## Projection

A successful scope carries:

- contract name and version;
- deterministic scope identity;
- course ID and title;
- exact manifest identity and manifest-byte SHA-256;
- exact source-inventory identity;
- total selection count; and
- an ordered immutable tuple of selections.

Every `CourseReferenceSelection` has its own deterministic identity bound to
the exact course, manifest, inventory, ordinal, inclusion origin, role, display
locator/status, and opaque References locator binding. `observed` means the
display key was preserved from the manuscript. `proposed-noncanonical` means it
came from the directed candidate scope. Neither value means that References
accepted the citekey.

The role is currently limited to `manuscript-bibliography`. It conveys course
scope only and makes no claim-support, scientific-review, rights, publication,
or evidence-admission assertion.

## Navigation

The ordered tuple is the only navigation authority. The scope derives previous
and next selection identities without mutable cursor state. `window` returns a
deterministic zero-based bounded slice with previous and next offsets. Window
limits are positive and at most 100; offsets beyond the total fail closed.

The serialized scope adds derived previous/next IDs to every selection. These
values are presentation/navigation data, not References authority.

## Composition boundary

An API may left-join every ordered selection to a current References projection
using the opaque locator. It must retain all course selections and represent a
missing or unresolved References projection explicitly; it must not drop a
selection or fall back to matching a proposed citekey.

References remains responsible for display citation metadata, identity and
citekey decisions, document bindings, source assets, and owner operations. API
mutations route to References. Courses should rebuild a new scope when either
its manifest/inventory revisions or locator-binding tuple changes; prior scope
and selection identities then remain immutable historical identities.
