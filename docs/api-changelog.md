# API changelog

## 2026-10-05 — authenticated live source feed

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
