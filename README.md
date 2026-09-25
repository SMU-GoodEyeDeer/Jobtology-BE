# Jobtology BE

Python/FastAPI backend for personalized capability analysis and roadmaps.

## Status and documentation

The current worktree implements the local-JSON M1–M5 product flow and the additive, read-only
native `/api/v2` catalog. Remote deployment, Google sign-in, remote publication/revocation
integration, and native editorial analysis or route planning remain pending or intentionally
unsupported. These documents do not certify a remote deployment or a live authenticated flow.

- [Current v2 architecture and product requirements](docs/plan.md)
- [Korean frontend integration guide](docs/fe-integration.md), packaged in the wheel and served
  at `/api/guide`
- [Native Neo4j source contract](docs/neo4j-source-contract.md)
- [Historical Neo4j/local PostgreSQL verification record](docs/neo4j-verification.md)
- [Goldship deployment and environment guide (Korean)](docs/goldship-deployment.md)

Google login is currently disabled (`JOBTOLOGY_AUTH_ENABLED=false`). Product routes, including
the native catalog routes, remain fail-closed and return `401 UNAUTHENTICATED` without an
authenticated session. There is no development login endpoint, trusted identity header, or
fixture mode that authenticates product requests.

## Development

```sh
uv sync --dev
uv run uvicorn jobtology_be.main:app --reload
```

- Swagger UI: http://localhost:8000/api/docs
- OpenAPI: http://localhost:8000/api/openapi.json
- Korean FE integration guide: http://localhost:8000/api/guide (source: [docs/fe-integration.md](docs/fe-integration.md))
- Liveness: `/api/v1/health/live` (not database readiness)

To enable explicitly labeled FE fixtures:

```sh
JOBTOLOGY_ENABLE_FIXTURES=true uv run uvicorn jobtology_be.main:app --reload
```

Fixture endpoints: `/api/v1/dev/analysis`, `/api/v1/dev/route-proposal`.
They contain illustrative data only and do not replace authenticated, persisted product behavior.
Production configuration rejects fixture mode.

## Verification

```sh
uv run pytest
uv run ruff check .
uv lock --check
uv build
```

PostgreSQL acceptance checks are opt-in and require a freshly provisioned disposable database.
The former disposable PostgreSQL environment has been removed; this documentation update does
not claim to have rerun it. Dated `306`-test evidence and its known limits are retained in
[docs/neo4j-verification.md](docs/neo4j-verification.md).

## Deployment

Railpack configuration, runtime variables, migration/worker sequencing, private routing, and
manual smoke/rollback steps are in [deploy/README.md](deploy/README.md). Deployment remains a
repository-owner action; no documentation change performs or certifies a remote deployment.
