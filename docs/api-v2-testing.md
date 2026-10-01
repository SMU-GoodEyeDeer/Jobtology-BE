# Test the source-only `/api/v2/catalog` locally

Run these commands from the Jobtology-BE repository root. This guide exercises
the approved PostgreSQL source catalog, not product analysis or the separate
native Neo4j `/api/v2` endpoints. No production database or login is required.

## Automated checks

The unit/HTTP contract tests use a test-injected principal and reader; they do
not create a development login or change the shipped authentication behavior.

```sh
uv sync --dev
uv run pytest -q tests/test_source_catalog_api.py
```

For the real database boundary, start Docker and keep the sibling
`../Jobtology-DB/hop/ontology/tests/run.py` checkout available, then run:

```sh
docker info --format '{{.ServerVersion}}'
uv run pytest -q -rs tests/test_source_catalog_integration.py
```

The integration test creates and removes its own randomly named PostgreSQL
container, loads synthetic six-source data, grants the actual restricted
`jobtology_catalog_reader` role, and connects through the real read-only SQL
functions and HTTP routes. It publishes an ephemeral loopback port; on a
Colima Docker context it creates a temporary local SSH tunnel because VM-local
loopback ports are not forwarded to macOS. Both database logins receive
random per-test passwords. It actively checks reachability for up to 15 seconds
and fails if the connection cannot be made. A Docker-unavailable **skip is not
a PostgreSQL pass**. Its synthetic `graph_load` approval record tests the
source-only gate, not native Neo4j loading or production approval.

CI checks out Jobtology-BE and `SMU-GoodEyeDeer/Jobtology-DB` as siblings under
the runner workspace so the same relative fixture path works there. The DB
checkout is pinned to an exact commit SHA in `.github/workflows/ci.yml`, not a
moving branch. Update that SHA deliberately when the approved DB fixture
changes; a missing fixture fails the integration test rather than skipping it.

Run the full backend quality gates after that:

```sh
uv run pytest -q -rs
uv run ruff check .
uv lock --check
uv build
```

## What the HTTP statuses mean

| Status | Local observation | Interpretation |
|---|---|---|
| `401 UNAUTHENTICATED` | Start the app with default settings and call the route without a session | Authentication is fail-closed, regardless of catalog DSN or approval. |
| `503 DATA_UNAVAILABLE` | Integration test's injected principal before approval, or an authenticated request without a catalog connection / with stale approval | The separate catalog gate is closed; not a login failure. |
| `200` | Integration test's injected principal, restricted reader, and disposable approved source-only release | Read-only catalog data is available for that isolated fixture, not evidence that the deployed service is enabled. |
| `422 VALIDATION_ERROR` | Unknown/repeated query key, `preview`, or blank `release_id` | Input rejected without a preview bypass. |

Try the default unauthenticated response yourself in two terminals:

```sh
uv run uvicorn jobtology_be.main:app --host 127.0.0.1 --port 8000
```

```sh
curl -i http://127.0.0.1:8000/api/v2/catalog/summary
curl -i http://127.0.0.1:8000/api/openapi.json
```

The first call returns `401`; OpenAPI documents the contract but does not
authenticate you or open the catalog. With the default
`JOBTOLOGY_AUTH_ENABLED=false`, setting `JOBTOLOGY_CATALOG_DATABASE_URL` alone
still cannot produce a `200` over HTTP. Do not use a trusted-header shortcut,
fixture login, or preview parameter: none is shipped. To enable a real
deployment later, an actual authenticated session, the separate restricted
catalog DSN, and operator-approved verified source release are all required.
See [the catalog contract](source-catalog-api.md) for routes, projections and
error semantics and the sibling DB runbook for approval prerequisites.
