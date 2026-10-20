# Railpack deployment (manual operator runbook)

## Status and responsibility

This is an operational guide for the repository owner. It does not perform a deployment, expose
secrets, or replace live verification. Goldship uses the CI/Coolify flow documented below;
Google sign-in is implemented but remains disabled until its OAuth client credentials are configured.
Guest authentication has been removed. The worktree already implements the local-JSON
M1–M5 product flow and additive native v2 catalog; promote only a commit whose intended scope has
passed its own current release gates. A passing build does not prove live Google consent or native
source editorial analysis/route planning.

Railpack builds the image from the repository root using
[`railpack.json`](../railpack.json), `.python-version`, `pyproject.toml`, and the committed
`uv.lock`. The hosting platform owns routing, runtime secrets, resource limits, deployment
triggers, and rollback.

The [Goldship deployment and environment guide](../docs/goldship-deployment.md) records the
observed host topology and Coolify setup; use its smoke checks to verify a deployment.

Document processing is copied into the BE source and remains compatible with Python 3.12.
`railpack.json` also requests Java 25 and .NET 8 in this image for the bundled converters,
plus ICU/fontconfig. `.mise.toml` keeps .NET installs in the runtime-copied Mise tree.
See [document processing](../docs/document-processing.md) for the installed-wheel checks.
This library integration does not enable public résumé uploads.

## Pre-deploy gate

Run against the exact commit to promote:

```sh
uv sync --locked --dev
uv run ruff check .
uv run pytest
uv lock --check
uv build
```

Use a fresh disposable PostgreSQL environment only when the intended gate includes opt-in
acceptance tests. The historical disposable database has been removed, so a normal documentation
or unit-test pass is not PostgreSQL acceptance evidence.

Configure the platform to wait for the tested commit's CI checks; a push-triggered build alone
does not prove that those checks passed.

## Build and start

1. Select Railpack and the repository root as the build directory.
2. Pin a supported Railpack builder version in the platform.
3. Use the start command in `railpack.json`; remove stale Dockerfile or start-command overrides.
4. Bind the assigned internal `PORT` (default `8000`) and configure the HTTP health check at
   `/api/v1/health/ready`.

The liveness endpoint proves that the process is running, not that the database, corpus source,
or worker is ready for product traffic.
The readiness endpoint checks the application database, required role catalog,
and required in-process worker heartbeat; it returns 503 while any required
component is unavailable. Keep `/api/v1/health/live` for process-only probes.

## Runtime configuration

Set real values only in the hosting platform's secret/configuration store. Never place them in the
repository, build context, logs, or this document.

| Variable | Production expectation | Notes |
| --- | --- | --- |
| `JOBTOLOGY_ENVIRONMENT` | `production` | Production rejects fixtures and mock samples. |
| `JOBTOLOGY_ENABLE_FIXTURES` | `false` | Legacy preview routes are development-only. |
| `JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES` | `false` | FE mock samples are development-only. |
| `JOBTOLOGY_CORS_ORIGINS` | Explicit JSON origin list | Include the exact `JOBTOLOGY_FRONTEND_URL` when Google authentication is enabled; credentialed CORS never accepts `*`. |
| `PORT` | Platform internal port | Defaults to `8000` if the platform does not set it. |
| `JOBTOLOGY_DATABASE_URL` | Required | Application-owned PostgreSQL only; do not point migrations at an ingestion or corpus database. |
| `JOBTOLOGY_CORPUS_SNAPSHOT_PATH` | Required for local-JSON analysis/worker operation | Mount or otherwise provide the snapshot outside the image and keep API and worker on the same version. |
| `JOBTOLOGY_CORPUS_SOURCE` | `local_json` unless a bounded native catalog is intentionally configured | `neo4j_query_api` supports only the documented v2 catalog reads, not native planning. |
| `JOBTOLOGY_DB_LINK`, `JOBTOLOGY_DB_PASSWORD`, `JOBTOLOGY_DB_PROTOCOL` | Only for an explicitly configured native catalog source | Keep credentials in platform secrets; no implicit remote-corpus or planner integration exists. |
| `JOBTOLOGY_AUTH_ENABLED` | `false` while staging; `true` after Google configuration and migrations | Disabled authentication returns `401` for account APIs and `503` for Google login/callback without creating users. |
| `JOBTOLOGY_GUEST_SESSIONS_ENABLED` | `false` or omit | Guest authentication is removed; `true` is rejected. |
| `JOBTOLOGY_GOOGLE_CLIENT_ID` | Google Web application OAuth client ID | Required when authentication is enabled. |
| `JOBTOLOGY_GOOGLE_CLIENT_SECRET` | Matching client secret | Backend runtime secret only. |
| `JOBTOLOGY_GOOGLE_REDIRECT_URI` | `https://jobtology.yeongmin.net/api/v1/auth/google/callback` on Goldship | Register the exact URI in Google Cloud. |
| `JOBTOLOGY_FRONTEND_URL` | `https://jobtology.yeongmin.net` on Goldship | Exact origin, also listed in CORS. |

The redirect and frontend URLs can be staged with `JOBTOLOGY_AUTH_ENABLED=false` before the
client ID and secret are available. Enable authentication only after the complete Google settings
are ready; enabled production authentication requires HTTPS. See the
[Google sign-in guide](../docs/google-auth.md) for consent-screen and browser checks.

Do not enable fixture or mock flags in production to work around authentication. They neither
create users nor authenticate product routes.

## Database and worker release steps

For a product environment with application persistence:

1. Provision an application-owned PostgreSQL database and set `JOBTOLOGY_DATABASE_URL`.
2. Run the migration exactly once as a release task before promoting traffic:

   ```sh
   uv run alembic upgrade head
   ```

   The current head is `20261020_01`, adding weekly-plan versions, completed
   schedule snapshots and recompute failure diagnostics. Apply it before serving
   this image; otherwise reads referencing the new columns fail. Common `users`, `profiles`, and
   `auth_sessions` tables remain in use by Google accounts.
   Do not run it independently in every API replica. Preserve backward compatibility before
   relying on an image rollback.
3. In the separate catalog database, apply the additive Jobtology-DB SQL
   `031_multi_source_live_postings.sql` and `032_inferred_occupation_context.sql`
   as the existing owner/administrator. They do not change the approved
   product-role artifact. See [the source API guide](../docs/source-catalog-api.md).
4. Goldship uses `JOBTOLOGY_INPROCESS_WORKER_ENABLED=true`; readiness monitors
   its supervised polling loop. If using an external batch worker instead,
   disable the in-process worker flag and run the same image and settings:

   ```sh
   uv run python -m jobtology_be.workers.main
   ```

   The worker is a one-shot batch entrypoint, not a continuously polling daemon. Schedule repeated
   batches externally and give the worker its own concurrency and restart policy.

## Routing and manual smoke checks

Route `/api/*` on the frontend origin to the service. Every API and documentation surface lives
under `/api` (`/api/docs`, `/api/redoc`, `/api/openapi.json`, `/api/guide`), so a single
path-prefix rule covers them; do not strip the `/api` prefix at the proxy.

After the owner deploys, check the following at the service origin:

| Request | Expected result |
| --- | --- |
| `GET /api/v1/health/live` | `200` process liveness response |
| `GET /api/v1/health/ready` | `200` only when required runtime components are ready |
| `GET /api/docs`, `GET /api/redoc`, `GET /api/openapi.json` | `200` API documentation surfaces |
| `GET /api/guide` | `200` Korean wheel-backed integration guide |
| An unauthenticated product request | `401 UNAUTHENTICATED` envelope |
| `GET /api/v1/auth/session` without a valid Google-backed session | `401`; no anonymous account or session cookie is created |
| `GET /api/v1/auth/google/login` while authentication is disabled | `503` |
| `GET /api/v1/auth/google/login` after configuration is enabled | Redirect to Google consent |

The expected `401` proves the fail-closed boundary only. It does not prove Google login,
authenticated user behavior, remote corpus publication, native editorial analysis, or route
planning.

## Rollback and source limits

Retain the previous image before promotion. Roll back only when the deployed schema remains
backward-compatible; do not blindly run a production `alembic downgrade`. Repeat liveness and
fail-closed smoke checks after rollback.

The native source is a bounded read-only catalog. `READY` is not editorial `PUBLISHED`, an
accepted alignment is not a required competency, and native catalog availability does not enable
native analysis or planning. See [the v2 requirements](../docs/plan.md) and
[native source contract](../docs/neo4j-source-contract.md) before changing source configuration.
