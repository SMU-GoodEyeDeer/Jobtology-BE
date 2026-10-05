# Approved source-only catalog HTTP API

The PostgreSQL source catalog is independent of the existing Neo4j `/api/v2/*` and
product `/api/v1/*` routes. All routes below require the real authenticated session
principal; absent authentication returns the standard `401 UNAUTHENTICATED` envelope.
No source ID is a product canonical occupation ID. Source records do not authorize
analysis, recommendations, or publication.

## Configuration and lifecycle

By default no catalog connection exists and authenticated requests return 503.
`JOBTOLOGY_CATALOG_DATABASE_URL` is a separate secret, never inferred from
`JOBTOLOGY_DATABASE_URL`. Configure an asyncpg URL with the *restricted*
`jobtology_catalog_reader` login after applying DB `019_catalog_approval.sql` and
`catalog_reader_grants.psql`, provisioning reader authentication, and independently
approving a verified graph load according to the [DB runbook](../../Jobtology-DB/hop/ontology/catalog.md).
Example shape (not a credential):

```text
JOBTOLOGY_CATALOG_DATABASE_URL=postgresql+asyncpg://jobtology_catalog_reader:<secret>@<host>/<database>
```

The reader role must have only the five catalog function EXECUTE grants and no
`ontology` schema access. The API never calls the approval function and cannot
open the analytics gate. Its dedicated pool is limited to four connections,
five-second acquisition/connect/command timeouts, and one read-only,
READ COMMITTED transaction per HTTP read, as required by the sealed-catalog gate.
Each call remains read-only and pins the approved release; sealed data cannot be
edited through normal database writes. No caller-supplied SQL is accepted.
App lifespan disposes the pool. A `PREPARING` release can be read only after
separate operator approval; a new/unverified/failed load closes the gate.

## Routes

All query strings are closed: unknown keys (including `preview=true`), repeated keys,
and a present but empty/whitespace-only `release_id` return 422.
`release_id` is optional; if present it must equal the currently approved pointer.
There is no preview fallback. Lists use `limit=100` (1–100) and `offset=0` (nonnegative).
Every paginated request must pin the same release ID; the DB gate is checked again
on each page and may close between requests.

| GET path | Shape |
|---|---|
| `/api/v2/catalog/summary?release_id=...` | context, `entity_counts`, `posting_selection_outcomes` |
| `/api/v2/catalog/entities?kind=occupation&limit=20&offset=0&release_id=...` | context, `entity_kind`, `limit`, `offset`, `items` |
| `/api/v2/catalog/entities/{entity_id:path}?release_id=...` | context, `entity` with allowlisted `source_facts` |
| `/api/v2/catalog/relations?entity_id=...&limit=20&offset=0&release_id=...` | context, `entity_id`, `limit`, `offset`, `items` |

For URN-like IDs with colons and slashes, URL-encode the path segment when possible
(clients/proxies may normalize encoded slashes); the `relations` route uses a query
parameter rather than an ambiguous path suffix. Named list aliases are
`occupations`, `competencies`, `organizations`, `postings`, `qualifications`,
`exam-sessions`, and `career-ranks` under `/api/v2/catalog/`. Each returns the
same paginated shape as `entities` with a fixed kind. `kind` additionally accepts
`ncsUnitFamily`, `ncsClass`, and `conceptScheme`.

Example summary (illustrative counts, not deployment evidence):

```json
{
  "contract_version": "hop-catalog-source-v1", "release_id": "release-verified-1",
  "data_as_of": "2026-09-30T00:00:00+00:00", "manifest_hash": "<manifest-hash>",
  "source_profile": {"kind": "SOURCE_ONLY", "analysis_available": false,
    "capabilities": ["entities", "source_relations"]},
  "entity_counts": {"occupation": 1},
  "posting_selection_outcomes": {"SELECTION_PENDING": 2}
}
```

Entity items contain only `entity_id`, `kind`, `code`, `scheme_id`, `revision_id`,
`name`, `payload_hash`. Detail adds `schema_version` and allowlisted `source_facts`
(e.g. `aliases`, source status/dates/IDs and version/rank identifiers). Relation
items contain only `relation_id`, `subject_id`, `predicate`, `object_id`,
`assertion_kind`, `acceptance_policy`. Unknown DB fields are dropped, not passed
through. No descriptions, eligibility text, evidence, reviewer data, raw
responses, private profile, or product analysis fields are returned.

Errors use the existing `{ "error": {"code", "message", "details", "request_id"} }`
envelope: unapproved/stale/unreachable 503; revoked/failed release 410;
entity absent from the approved release 404; invalid filters/pages 422.
DB exception text, SQL and credentials are not returned. Counts and posting
outcomes are inventory status, not semantic review or employment metrics.

## Live source feed (separate from the sealed release)

Authenticated `/api/v2/live/*` reads the **newest READY ingestion run per source**,
not the approved, sealed catalog release. Data can change between requests and
pages; `sources` identifies each contributing `source_id`, `run_id`, and
`data_as_of`. A live source record is not an editorial decision, eligibility
assessment, recommendation, or product occupation. It uses the same restricted
`JOBTOLOGY_CATALOG_DATABASE_URL` and engine as the catalog, with a read-only
READ COMMITTED transaction per read. Without configuration or a usable READY
run, requests fail closed with 503; unauthenticated requests return 401.

| GET path | Parameters | Response |
|---|---|---|
| `/api/v2/live/postings` | `q`, `ncs_category`, `region`, `open_on=YYYY-MM-DD`, `limit=20` (1–100), `offset=0` (nonnegative) | `contract_version`, `sources`, `filters`, `limit`, `offset`, `total`, `items` |
| `/api/v2/live/postings/{posting_id}` | source posting ID (URL-encode slashes where possible) | `contract_version`, `sources`, `item` |
| `/api/v2/live/exam-sessions` | `qualification`, `from=YYYY-MM-DD`, `to=YYYY-MM-DD`, `limit=20` (1–100), `offset=0` (nonnegative) | `contract_version`, `sources`, `filters`, `limit`, `offset`, `total`, `items` |
| `/api/v2/live/ncs-demand` | `ncs_prefix` (2–8 digits), `limit=20` (1–100), `offset=0` (nonnegative) | reviewed NCS linkage inventory (`contract_version`, `sources`, `review`, `filters`, `limit`, `offset`, `total`, `items`) |

`contract_version` is `hop-live-source-v1`. Posting items expose `posting_id`,
`title`, `organization_code`, `organization_name`, `date_posted`, `closing_date`,
`ongoing`, `regions`, `employment_types`, `recruitment_type`, `education`,
`ncs_categories` (`code`, `name`), `headcount`, and `source_url`. Exam items expose
`qualification_code`, `qualification_name`, `year`, `round`, `category_code`,
`name`, and `written`/`practical` date groups; each group has
`registration_start`, `registration_end`, `exam_start`, `exam_end`, `result_date`.
Dates are `YYYY-MM-DD` or null. Unknown DB response fields are omitted.
Unknown or repeated query keys, invalid filters or pages return 422; missing
posting returns 404; source/driver/validation failures return 503. Errors use
the standard envelope without private database messages. There is no
eligibility, preference, selection, or disqualification text in these responses.

NCS demand items provide `competency_code`, `competency_name`,
`ncs_occupation_code`, `ncs_occupation_name`, distinct posting count `postings`,
`links`, up to three ordered evidence summaries (`source_id`,
`publication_id`, `created_at`, `source_posting_id`, `title`, `position`, `duty`), and
`related_qualifications` (`qualification_code`, `qualification_name`).
The response contract version is `hop-live-ncs-demand-v1`; `sources` identifies
the newest READY publication with attributable items per posting source, with
`posting_source`, `publication_id`, `created_at`, the contributing source `run_id`,
and `is_latest_publication` (whether it is the newest READY publication pinning
or having that source). A newer publication with zero attributable items does
not hide older evidence. Such evidence is **historical, not today's demand**;
show its publication date rather than labeling it current. `sources` and
`review.link_reviewer_kinds` reports counts by reviewer kind, never identities,
decision IDs, notes, or reasons. Invalid prefixes return 422. Evidence is a
source linkage, not a job requirement or user eligibility assessment.

## Verification limits

Run the BE checks from this repository root:

```sh
uv sync --dev
uv run pytest -q tests/test_source_catalog_api.py
uv run pytest -q -rs tests/test_source_catalog_integration.py
uv run pytest -q
uv run ruff check .
uv build
```

The API unit test command tests real FastAPI routes with a **test-injected
principal and reader**, not a login bypass. It covers `401`, allowlisted projection, strict
query keys and pagination, SQL error mapping, and read-only bound SQL calls.
The integration test requires a running Docker daemon and sibling
`Jobtology-DB/hop/ontology/tests/run.py`. It creates a disposable PostgreSQL
container with an ephemeral loopback port (and a temporary Colima host tunnel
where necessary), loads synthetic sources, and tests the
restricted reader's privileges and HTTP `503` before approval, `200` for an
approved source-only release, `503` after a stale load, and `410` after
revocation. A Docker-less run skips that integration test; a daemon whose
published loopback port is unreachable fails it. Treat a skip as
**not verified**, not a pass of PostgreSQL behavior. The synthetic graph-load
record is not evidence of a native Neo4j load.

For a local API smoke check without an authenticated session:

```sh
uv run uvicorn jobtology_be.main:app --reload
curl -i http://localhost:8000/api/v2/catalog/summary
curl -i http://localhost:8000/api/openapi.json
```

With authentication disabled (the current default), the catalog request
returns `401 UNAUTHENTICATED` even if a catalog DSN is configured. OpenAPI
lists the route contract but does not imply an approved catalog or a working
user session. Do not invent a development login or assume a `200` can be
smoke-tested over HTTP in that configuration. Before any operational reader
is enabled, the DB runbook's source-only approval and deployment-specific
native graph verification must pass separately; the BE tests cannot prove
either a production approval or an open analytics gate.
