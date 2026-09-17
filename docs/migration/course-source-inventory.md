# Course Source Inventory

## Status

Initial filesystem inventory and DLSU legacy-copy pass.

## Repository convention

Course repositories use:

```text
~/repos/<institution>_<COURSE_CODE>/
└── legacy/
    ├── README.md
    ├── COPY_REPORT.json
    ├── MANIFEST.sha256
    └── sources/<source-label>/...
```

Source labels preserve where each copy came from. Legacy files are copied
without normalization. Nested Git metadata, environments, dependency/tool
caches, OS metadata, Office temporary files, and identifiable final-grade
records are excluded.

Student submission directories found in `~/Downloads` are not imported.

## DLSU repositories created or updated

| Repository | State |
|---|---|
| `~/repos/dlsu_SOLST01` | created; legacy sources copied |
| `~/repos/dlsu_MTSCI01` | created; legacy sources copied |
| `~/repos/dlsu_MTSCI02` | created; legacy sources copied |
| `~/repos/dlsu_MTSCI03` | created; legacy sources copied |
| `~/repos/dlsu_ENGPHS1` | created; legacy sources copied |
| `~/repos/dlsu_PHY673M` | created; legacy sources copied |
| `~/repos/dlsu_SOLST02` | created; legacy sources copied |
| `~/repos/dlsu_INSTR01` | created; legacy sources copied |
| `~/repos/dlsu_PHYS102A` | created; legacy sources copied |
| `~/repos/dlsu_PHYS104` | created; legacy sources copied |
| `~/repos/dlsu_SEMPHY01` | created; legacy sources copied from `SEMPY01` archive |
| `~/repos/dlsu_LBYINS1` | existing repository; legacy source copies added |

## DLSU source families found

- Google Drive, Project Koios account: `My Drive/05_courses/`
- Google Drive, Project Koios account: `My Drive/DLSU/05_courses/`
- Google Drive, DLSU account: `My Drive/teaching/05_courses/`
- Google Drive historical collection: `My Drive/DLSU/05_courses/archive_courses/`
- Dropbox vault: `eugene_vault/20_courses/`
- Dropbox DLSU course templates: `eugene_vault/DLSU/Courses/`
- Local course repositories under `~/repos/` and `~/repos2/`
- Course-owned loose files in `~/Downloads/`
- Published/rendered remnants under `~/repos/eugeneragasa/_site/courses/`
- Historical SOLST01 page under
  `~/repos/eugeneragasa/code/archive/projectkoios-www/www/courses/`

Rendered website remnants were inventoried but not copied as canonical course
sources in the initial pass.

## DLSU unresolved or shared source groups

These require a course-identity decision before migration:

- `archive_courses/common`
- `archive_courses/dlsuphysics`
- `archive_courses/em`
- `archive_courses/Cornell_PHYS3330_instrumentation`
- `archive_courses/Cornell_PHYS3360_instrumentation`
- Dropbox `20_courses/qm`
- Dropbox `DLSU/Courses/StatisticalMechanics`
- shared notebooks under `~/repos/physkit/notebooks/solidstate/`
- shared notebooks under `~/repos/physkit/notebooks/mtsci02/`
- Simon solid-state lecture transcripts under the archived Project Koios
  development tree

The Cornell and Simon materials appear to be external reference material, not
course-instance authority.

## University of the Pacific candidates

Found under Dropbox `Courses_Pacific/`:

- `ECPE223`
- `ECPE233`
- `ECPE293A`
- `ECPE293B`
- `ENGR045`
- `ENGR100`
- `ENGR120`
- `ENGR209`
- `ENGR219`
- `ENGR295`
- `MATH057`
- `MATH157`
- `MECH104`
- `MECH175`
- `MECH200`
- `MECH202`
- `MECH204`
- `PHYS161`
- `PHYS181`
- `PHYS183`

Proposed names are `pacific_<COURSE_CODE>`. Three sanitization canaries now
exist:

- `pacific_ENGR219`: 202 files classified, 30 retained, and 172 copied to the
  local, Git-ignored private-source tree.
- `pacific_MATH157`: 55 files classified, 22 retained, and 33 copied to the
  local, Git-ignored private-source tree.
- `pacific_MATH057`: 31 files classified and all 31 copied to the local,
  Git-ignored private-source tree; no confidently student-authored source was
  identified.

The remaining Pacific courses have not been copied because the collection may
mix instructor material, coursework, team artifacts, textbooks, and solution
manuals. Each requires an explicit allowlist policy before migration.

## University of Florida candidates

Found under Dropbox `Courses_UF/`:

- `EMA6114`
- `EMA6136`
- `EMA6313`
- `EMA6316`
- `EMA6803`
- `EMA6808`
- `EMA6938`
- `EML6934`
- historical computational-materials and heat-transport collections whose
  formal course codes are not yet established

Proposed names are `uf_<COURSE_CODE>`. These have not been copied because the
collection appears to contain student-era homework, tests, team material, and
software binaries in addition to course information.

## Loose syllabus/reference candidates

`~/Downloads` contains additional DLSU and external syllabus files, including
EMA course syllabi and files with ambiguous numeric course identities. They
remain uncopied until institution, course ownership, and intended repository
are established.

## Safety hold

The inventory found identifiable grades and student submissions for SOLST01 and
MTSCI02. Those records remain in their original locations and are not included
in the new repositories. A separate private-record policy is required before
any migration of student records.
