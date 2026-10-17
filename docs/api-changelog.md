# API changelog

## 2026-10-18 — weekly roadmap plans

- Added authenticated `GET`, `PUT`, and `DELETE /api/v1/roadmaps/{roadmap_id}/plan`.
  PUT/DELETE use the existing session-bound CSRF protection. Foreign or missing roadmaps
  return 404; invalid permutations, unknown hour keys, and invalid strict inputs return 422.
- Plans recommend level/demand/base-code order and continuously pack hours around rest weeks.
  Dates use Asia/Seoul; the start is the Monday containing the roadmap creation timestamp.
  Missing metadata falls back to 20 hours, CORE phase, and nullable display fields, not 503.
- User capacity, order, rest weeks, and differing step-hour overrides are stored separately;
  existing roadmap responses, step states, and product-role draft digests are unchanged.
- **Deployment required:** run `uv run alembic upgrade head` before serving this release
  (`20261018_01` creates `roadmap_plan_overrides`). No new artifact approval is needed.

## 2026-10-17 — readable qualification labels

- `result.skills[].qualifications[]` adds `display_name` (always present) and `kind`
  (`NATIONAL_TECHNICAL` | `WORK_LEARNING` | null). Work-learning standard names
  (`SW개발_L5_25V2`) read as `SW개발 5수준`; older standards of the same family and
  level are dropped, so the array can be shorter than before. National qualifications
  sort first. `code`/`name` are unchanged; the product-role digest is unchanged.

## 2026-10-17 — skill-detail panel reads

- `GET /api/v1/analyses/{analysis_id}` adds `requirement_key`, `ncs_base_code`,
  `status`, `ncs_level`, `estimated_hours`, `hours_basis`, and `qualifications`
  (`code`, nullable `name`) to `result.skills[]`. Existing fields, ordering, and
  exclusion of satisfied requirements are unchanged. Missing metadata yields null
  detail values and an empty qualification array, while stored key/status remain.
- Base codes and selected-version qualification mappings are display projections
  of already-hashed inputs; the product-role draft document/digest and existing
  artifact approval are unchanged. No new approval is needed for this API change.
- Added authenticated `GET /api/v2/live/ncs-units/{base_code}` through restricted,
  read-only `catalog.live_ncs_unit_v1(text)`. It returns the existing live envelope
  with `item: {base_code, code, name, level, definition}`; level/definition may be
  null. Exactly 10 ASCII digits and no query parameters are allowed (422).
  Missing item is 404; missing configuration/function/grant or read failure is 503.

## Unreleased (ai_onboarding) — chat-driven onboarding

- Route preferences are optional for analysis; users without them are planned with
  10 hours/week and REGULAR budget defaults.
- `POST /api/v1/chat/messages` accepts `mode: "onboarding"` to ask about uncovered capability
  areas one at a time instead of counseling.
- `GET /api/v1/analyses/{id}` `result.skills[]` now fills `demand_pct` (share of the role's
  distinct NCS-linked postings that link the skill; null below 5 linked postings) and adds
  `demand_postings` and `demand_base`. Requires Jobtology-DB migration
  `028_product_role_demand_base.sql` and a new product-role artifact approval.

## 2026-10-06 — faster analysis reads, solver settings, AI chat

- `POST /api/v1/analyses` returns the existing PENDING/RUNNING/READY request for the same goal
  and profile version instead of enqueueing a new recompute.
- The in-process worker uses `JOBTOLOGY_SOLVER_WORKER_COUNT` (default 8) CP-SAT workers and
  `JOBTOLOGY_SOLVER_TIME_LIMIT_SECONDS` (default 10). When a full-coverage solve times out, the
  planner now falls back to the best route instead of failing: a regular route if it still covers
  every required requirement, otherwise `PARTIAL`.
- Added `GET /api/v1/chat/status` and `POST /api/v1/chat/messages` (stateless; capability
  candidates restricted to the active goal's approved requirements with verbatim user quotes).
  Configure with `JOBTOLOGY_LLM_PROVIDER` (`disabled` default, `openai_api_key`, `chatgpt_oauth`).
- ChatGPT OAuth credentials are stored AES-256-GCM encrypted in the application database
  (`llm_credentials`, migration `20261006_01`) and refreshed in place; manage them with
  `python -m jobtology_be.llm.credentials_cli`.

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
