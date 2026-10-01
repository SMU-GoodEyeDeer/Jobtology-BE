# Four-role editorial drafts (not published)

`config/editorial/four_roles.v1.json` is an unreviewed assistant-authored working draft for
`AI_ENGINEER`, `BACKEND_DEVELOPER`, `FRONTEND_DEVELOPER`, and `DATA_ANALYST`. It is **not** a
published corpus release, approved occupational standard, or input to analysis/route planning.
No human acceptance, source verification, NCS mapping, course credential, employment statistic,
or price has been asserted.

## File contract

- Top-level `version` is exactly `1`; `occupations` contains each of the four IDs exactly once.
- Each occupation has `status: "DRAFT"`, `authored_by: "ASSISTANT"`, and `reviewed_at: null`.
  Requirements carry a key, label, necessity, suggested learning outcome, and `source_refs`.
  Activities carry an action ID, positive revision and estimated hours, project/study kind,
  nonempty outcome keys and completion criteria, prerequisite IDs, `source_refs`, cost, and
  foundational flag. Hours and outcomes are **estimates/suggestions**, not guarantees.
- `source_refs` may be empty, and are provenance leads only. They are **not** the
  `EditorialRequirement.support_refs` or `ActivityTemplate.support_refs` required by
  `PublishedCorpusSnapshot`. `cost: {"kind": "UNKNOWN"}` means no verified estimate;
  `{"kind": "KNOWN_KRW", "krw": 0}` is a distinct, explicitly known zero cost. No draft
  currently claims a known price.
- Unknown fields, blank strings, unsupported status/version, duplicated IDs/keys, missing
  outcomes/criteria, missing prerequisites, and cycles fail import. The parser is frozen and
  exposes a deterministic SHA-256 content digest over its validated JSON representation.
  Changing draft content changes the digest; it is not a published release ID.

`load_drafts(path)` reads the specified local JSON file without database access. `DraftReadService`
projects the file into a restricted read shape containing `status: "DRAFT"` and
`analysis_ready: false`, without reviewer metadata or source refs. These objects have no
published-snapshot conversion method. The candidate template adapter is used *internally only*
for structural DAG validation; its validation-only marker is never offered to analysis, planning,
or publication as evidence.

## Review gate and API wiring

Before any separate published pipeline accepts a role, real human reviewers must verify each
requirement and activity against attributable sources, decide necessity, validate prerequisite
order, outcomes, effort and cost claims, and record their own review decision and timestamp.
Only a separate reviewed publication flow may create a release ID and published support refs;
copying `source_refs` or the validation marker is not approval. Re-run validation and product
tests after review. No publication or DB mutation is implemented here.

The authenticated draft-read API is mounted but disabled without an explicit file path. Set
`JOBTOLOGY_EDITORIAL_DRAFT_PATH` to the absolute path of a reviewed-for-structure JSON file,
for example `$(pwd)/config/editorial/four_roles.v1.json` in a development checkout. Deployments
must place and configure the file explicitly; it is not located relative to the process working
directory or implicitly included in a wheel. Startup fails if the configured file is missing or
invalid. `GET /api/v1/editorial/occupations` and
`GET /api/v1/editorial/occupations/{occupation_id}` require the existing authenticated principal;
an unknown ID is 404, and an unconfigured service is 503 after authentication. The response
retains `status: "DRAFT"` and `analysis_ready: false`. These routes neither publish nor feed
analysis/planning, and they have no public write/admin surface.
