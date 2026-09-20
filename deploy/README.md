# Railpack deployment

Build from the repository root using Railpack. Configuration lives in
[`railpack.json`](../railpack.json). The Python provider reads `.python-version`
and detects uv from `pyproject.toml` and `uv.lock`.

## Platform configuration

1. Select Railpack as the builder and the repository root as the build directory.
2. Deploy a commit whose CI checks have passed. Configure the platform's deployment
   trigger to wait for those checks; a push-triggered build alone does not enforce this.
3. Use the start command from `railpack.json`; remove any stale Dockerfile or start-command
   overrides in the platform settings.
4. Set runtime variables:
   - `JOBTOLOGY_ENVIRONMENT=production`
   - `JOBTOLOGY_ENABLE_FIXTURES=false`
   - `JOBTOLOGY_CORS_ORIGINS=[]` for same-origin-only browser access, or an explicit JSON
     list of allowed frontend origins when cross-origin access is intentional.
   - `PORT` to the platform's internal service port (defaults to 8000).
5. Configure HTTP health checks at `/api/v1/health/live` on the same port.
6. Route the frontend gateway's `/api/*` requests to this service over the private network.

Railpack builds the image; networking, deployment triggers, runtime secrets, resource
limits, and rollbacks are configured in the hosting platform. This configuration does
not assume Railway hosting or provision platform resources.

## Release flow

```text
PR → lint/tests → merge → Railpack image build for the tested commit
   → staging health/smoke checks → promote image → retain previous image
```

Pin the platform's Railpack version when selecting its supported builder release.
Keep `uv.lock` committed and verify it with `uv sync --locked --dev` in CI.
The current scaffold only exposes process liveness. Add readiness checks when database
adapters are implemented. Add a one-off migration task and a separate worker service
when their entrypoints exist; they are not part of the current startup command.

References:
- https://railpack.com/config/file/
- https://railpack.com/languages/python/
