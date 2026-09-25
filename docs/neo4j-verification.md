# Neo4j Native Catalog Verification Evidence (Historical Record)

Date: 2026-09-25. Historical result: **the bounded native-catalog, worker, and local
PostgreSQL verification passed.**

## Record status

This is dated QA evidence, not a current release or deployment certification. The disposable
PostgreSQL environment used for the record was removed after the run, and this documentation
consolidation does not claim to have recreated it or rerun PostgreSQL acceptance checks.

## Safety boundary

- PostgreSQL verification used only the disposable local container supplied for QA. Its
  loopback listener was healthy before the run. Each acceptance test creates an isolated,
  generated database and removes it during teardown.
- The live Neo4j check is opt-in through `JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE=1`. It loads
  credentials only through typed runtime `Settings`; this record contains no URI, password,
  identifier, response body, catalog value, reviewer name, or payload content.
- The live configuration assertion requires an HTTPS endpoint for
  `neo4j-1.yeongmin.net`. The application adapter keeps TLS verification enabled, redirects
  disabled, and the request timeout at ten seconds. No remote PostgreSQL or Neo4j write,
  migration, deployment, or authentication-provider interaction occurred.

## Live native API and repository evidence

The opt-in tests invoke the production composition and configured real repository with one
trusted test principal. No fake catalog, raw payload, source identifier, reviewer name, or
credential is printed or written to this record.

| Surface | Bound | Verified result |
| --- | ---: | --- |
| Unauthenticated `GET /api/v2/occupations` | 1 | `401`; only the error envelope key is inspected. |
| Trusted `GET /api/v2/occupations` | 2, then 1 at offset 1 | `200`; the offset page matches the first page's second item without emitting its ID. |
| Trusted `GET /api/v2/publications` | 1 | `200`; only `publication_id`, `source_state`, and capabilities are exposed. |
| Trusted `GET /api/v2/publications/{id}/alignments` | 1 | `200`; safe relation context is limited to publication, source-enrichment, source-posting, source-current, accepted, and safe competency fields. |
| Direct configured repository hash read | 1 publication, 3 alignments, 3 fixed enrichment reads | Each returned enrichment passed its UTF-8 SHA-256 model verification; the three alignment rows may reference the same enrichment, so this is not a claim of three distinct source records. |
| Trusted `POST /api/v1/analyses` under the native default | n/a | `503`; an actual local PostgreSQL before/after count proves no recompute request, context, outbox job, analysis, or route proposal was added. |

The final opt-in read-only smoke, native API, and native worker gate passed `6` tests with both
the live-acceptance and local-PostgreSQL flags enabled. Selected IDs were retained only in memory
to compose fixed paths and were never printed.

## Source-routing and worker evidence

`tests/test_neo4j_acceptance_worker.py` persists contexts through
`RecomputeContextDocument.model_dump(mode="json")` and runs the configured native worker against
a generated local PostgreSQL acceptance database with no local snapshot path:

- A historical payload with `snapshot_selection.source` absent remains `PENDING`, preserving
  legacy local replay instead of interpreting the current default as its source.
- An otherwise identical explicit `neo4j_query_api` payload is claimed and becomes
  `FAILED` with `NATIVE_SOURCE_UNSUPPORTED`.
- With `worker_batch_limit=1`, an older ineligible legacy job cannot consume the sole native
  worker slot; an eligible native job is still claimed and fails closed.
- An expired `LEASED` native outbox job is reclaimed by the same bounded native worker and
  terminalizes with `NATIVE_SOURCE_UNSUPPORTED`.
- A separate source-routing test also confirms that a persisted `local_json` context remains
  readable when the configured default is native.

## Final local PostgreSQL, lint, and build evidence

The final combined suite ran with both `JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE=1` and
`JOBTOLOGY_ACCEPTANCE_DATABASE_URL` set to the supplied disposable local PostgreSQL container:

- Collected: `306` tests.
- Passed: `306` tests.
- Skipped: `0` tests.
- Failed: `0` tests.
- Warnings: `8` deprecation warnings.
- Duration: `16.77` seconds.

The focused owned opt-in acceptance set also passed `6` tests. `uv run ruff check .` passed, and
`uv build` produced both the source distribution and wheel. A fresh managed installed-package
`basedpyright --level error` run, using the project Python interpreter, reported `0` errors across
the Neo4j corpus adapters, source factory/availability, catalog API and source models, worker
entrypoint, and recompute outbox persistence. A separate warning-level run reported `15` warnings,
so this record makes no warning-free static-analysis claim. MCP LSP diagnostics can lag filesystem
changes and are not the final static-analysis gate; the fresh CLI error-level result is.

## Historical flake note

The earlier dashboard invalidation failure sometimes lacked `proposal_id` after the worker run.
One debugger-backed and three serial isolated reruns all passed; the final full local-PostgreSQL
suite also passed. No persisted terminal state was captured during a failure, so its root cause
remains unclassified rather than attributed to the solver or silently retried.

## Current contract boundary and explicit non-claims

The supported native outcome is a bounded v2 catalog read. It is not native editorial analysis
or route planning, and a source label must not be promoted to an application planning fact.

- The native catalog read proves neither an editorial baseline nor a route-planning source.
  Native editorial analysis and planning remain unavailable by contract.
- No whole-publication canonical hash, source revocation policy, or upstream planner/template
  mapping was observed. The prior source record's raw UTF-8 enrichment hash observation remains
  sample-scoped and is not generalized here.
- `READY` remains a verbatim source state, not application `PUBLISHED`; historical enrichment
  topology is not called revocation; and an accepted alignment is not a capability,
  occupational requirement, score, or planning outcome.
