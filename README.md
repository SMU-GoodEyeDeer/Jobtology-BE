# Jobtology BE

Python/FastAPI scaffold for personalized capability analysis and roadmaps.
Design, functional specifications, schema and delivery milestones: [docs/plan.md](docs/plan.md).

## Development

```sh
uv sync --dev
uv run uvicorn jobtology_be.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- Liveness: `/api/v1/health/live` (not database readiness)

To enable explicitly labeled FE fixtures:

```sh
JOBTOLOGY_ENABLE_FIXTURES=true uv run uvicorn jobtology_be.main:app --reload
```

Fixture endpoints: `/api/v1/dev/analysis`, `/api/v1/dev/route-proposal`.
They contain illustrative data only, with no authentication, storage or actual calculation.
Production configuration rejects fixture mode. Business services are interfaces, not implementations.

```sh
uv run pytest
uv run ruff check .
```

## CI/CD and deployment

GitHub Actions runs lint and tests with the committed uv lockfile. Deployment uses
**Railpack**, configured by [`railpack.json`](railpack.json), with Python 3.12 and uv.
The start command binds to `0.0.0.0` and uses `PORT` (default: 8000).

Connect the deployment platform to the tested commit and configure runtime variables,
private routing, and health checks as described in [deploy/README.md](deploy/README.md).
Railpack builds the image; the hosting platform handles deployment and rollback.
