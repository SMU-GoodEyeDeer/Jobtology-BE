# API changelog

## 2026-10-05 — onboarding checklist and partial routes

- Added public `GET /api/v1/occupations/{occupation_id}/capability-checklist` with the
  approved onboarding checklist projected onto the current role release (404 when the
  occupation has no checklist).
- Added `PUT /api/v1/me/capabilities/onboarding`, replacing the user's previous onboarding
  answers in one transaction with a single profile-version bump. Answers are stored as
  `category: "onboarding"` self-reported capabilities; other capabilities are untouched.
- Route proposals may now report `feasibility: "PARTIAL"`: when no route can cover every
  required requirement before the target date, the planner returns the route covering the
  most required requirements. PARTIAL proposals can be saved, activated, and auto-created.
  Requires migration `20261005_01`.
- Capability edits that carry the previous recompute context now honor
  `JOBTOLOGY_CAPABILITY_LIST_AUTHORITATIVE`, so free-text capabilities no longer turn unmet
  requirements into NEEDS_INPUT after an edit.

## 2026-10-05 — direct step completion toggle

- `PATCH /api/v1/roadmaps/{id}/steps/{step_id}` also accepts `TODO → COMPLETED`
  and `COMPLETED → TODO`, matching the deployed browser's 완료/완료 취소 toggle.
  Reverting a completion still revokes the derived capability and recomputes.

## 2026-10-05 — FE-compatible product reads

- `GET /api/v1/occupations` is public (no session), with a `name` field from the
  configured role display names or an occupation-ID fallback. An unavailable
  published snapshot still returns 503.
- `GET /api/v1/recomputations/{id}` maps persisted READY to response state
  `COMPLETED` and adds `analysis_id` alongside `resulting_analysis_id`.
- `GET /api/v1/analyses/{id}` adds `result` with available coverage percentages
  and unmet skill summaries; missing metadata stays null and estimated hours
  are explicitly labeled. The existing `results` field remains intact.
- Saved roadmap list/detail add `status`/`version` aliases; steps add the
  proposal-matched `title` and first completion criterion as `description`.

## 2026-10-05 — authenticated live source feed

- Added `GET /api/v2/live/ncs-demand` for reviewed, source-linked NCS evidence
  with prefix filtering and pagination, without reviewer identity or private
  decision detail.
- Added `/api/v2/live/postings`, `/api/v2/live/postings/{posting_id}`, and
  `/api/v2/live/exam-sessions` as read-only, authenticated source views. They
  use the newest READY ingestion runs rather than the sealed catalog release;
  no editorial or eligibility fields are exposed.
- Reuses the restricted catalog reader connection and pool. Missing source or
  configuration returns 503; invalid/repeated filters return 422 and missing
  posting detail returns 404. See [live feed contract](source-catalog-api.md#live-source-feed-separate-from-the-sealed-release).

## 2026-10-01 — opt-in source reads

- Added authenticated `GET /api/v2/catalog/summary`, `/entities`,
  `/entities/{entity_id:path}`, `/relations?entity_id=...`, and fixed-kind list
  aliases. They call only the independently approved PostgreSQL source-only
  functions; no preview or write endpoint is exposed. Disabled by default.
- Added authenticated draft-only `GET /api/v1/editorial/occupations` and
  `GET /api/v1/editorial/occupations/{occupation_id}`. Explicit file path required;
  no published status, analysis or planning behavior follows from a draft.
- Added `JOBTOLOGY_CATALOG_DATABASE_URL` (secret, restricted reader only) and
  `JOBTOLOGY_EDITORIAL_DRAFT_PATH` (explicit disk path). Neither falls back to
  product DB, native Neo4j data, a packaged fixture, or the current directory.
- Existing `/api/v1/*` product and `/api/v2/*` Neo4j route shapes are unchanged.

See [catalog API contract](source-catalog-api.md) and
[local v2 testing guide](api-v2-testing.md) for schemas, limits and verification;
see [editorial draft notes](editorial-data.md) for editorial activation.
