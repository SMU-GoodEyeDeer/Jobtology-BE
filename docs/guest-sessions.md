# Opt-in browser guest sessions

Guest sessions are disabled by default. Enabling them does **not** enable Google
login or bypass product authentication. This feature uses existing PostgreSQL
`users`, `profiles`, and `auth_sessions` tables; no migration is introduced.

## Deployment flags

```sh
JOBTOLOGY_AUTH_ENABLED=false
JOBTOLOGY_GUEST_SESSIONS_ENABLED=true
JOBTOLOGY_GUEST_SESSION_MAX_NEW_PER_MINUTE=30
JOBTOLOGY_SESSION_TTL_SECONDS=604800
JOBTOLOGY_ENVIRONMENT=production
JOBTOLOGY_DATABASE_URL=postgresql+asyncpg://<application-role>:<password>@<host>/<database>
JOBTOLOGY_CORS_ORIGINS='["https://<frontend-host>"]'
```

The new issuance limit defaults to 30 and accepts integers from 1 through 1000.
Google and guest mode are mutually exclusive: enabling both fails startup.
Guest mode requires PostgreSQL; missing database configuration fails startup.
Partial unused Google settings do not require completing Google configuration
in guest mode, and unused redirect/frontend URLs do not influence guest cookie
policy. Outside guest mode, existing Google configuration validation remains.
Neither Google provider construction nor Google authorization/code exchange runs
in guest mode. Both Google login and callback endpoints return 503.

Deploy only after review and passing backend tests. Do not change production flags
merely to run tests. Production must already have the existing schema (including
`users.status`, `users.created_at`, `profiles.version`, and `auth_sessions.created_at`).
Keep the application database separate from any read-only source-catalog database.

For the approved guest deployment, `railpack.json` may explicitly opt in through
`deploy.variables` using string values `JOBTOLOGY_AUTH_ENABLED: "false"` and
`JOBTOLOGY_GUEST_SESSIONS_ENABLED: "true"`. This is deployment-specific, not a
change to the library default. Coolify runtime environment values can override
image ENV values: confirm the effective runtime flags after rollout. To turn it
off later, explicitly set **both** runtime flags to `"false"` (or remove the
image opt-in and redeploy); merely removing a runtime override can expose the
image's guest-enabled default again. The deployment owner performs this change;
backend test execution must not modify production configuration.

## Browser contract

1. Call `GET /api/v1/auth/session` with browser credentials enabled.
2. An absent, malformed, unknown, expired, or revoked cookie creates a **new** user
   UUID, profile (version 1), and session in one transaction. No Google identity
   row is created. There is no fixed guest user or trusted user-ID header.
3. The response is `{ "user_id": "<uuid>", "csrf_token": "<secret>",
   "profile_version": 1 }`, with `Cache-Control: no-store`.
4. A valid cookie reuses its existing UUID and CSRF token, performs no issuance,
   and returns the profile version currently persisted in PostgreSQL. It does
   not renew the cookie lifetime. Google-mode responses retain their old shape:
   absent `profile_version` is omitted rather than returned as null.
5. Product endpoints still return 401 without a valid session. Mutations require
   the returned CSRF token in `X-CSRF-Token`; missing/wrong tokens return 403.
   Existing CORS and mutation-origin checks are unchanged.
6. `POST /api/v1/auth/logout` requires CSRF, revokes the stored session, and clears
   the cookie. A later session GET may create a new guest, never recover the old
   guest's data. The revoked cookie also fails on product routes.

The cookie is `jobtology_session`, HttpOnly, SameSite=Lax, Path=/, with a seven-day
default TTL. Production always adds Secure; development/test permit local HTTP.
The two independent high-entropy cookie secrets are stored only as SHA-256 hashes
(session and CSRF) in PostgreSQL. The cookie is a bearer credential: possession
of another browser's cookie grants that browser's identity. Do not log cookies
or CSRF tokens. Clearing browser storage loses access; there is no guest recovery
or guest-to-Google account linking in this change.

## Protection and failure behavior

- Session bootstrap rejects an Origin outside the exact configured CORS origins,
  or `Sec-Fetch-Site: cross-site`, with 403 **before any guest creation**, even if
  a valid cookie exists. Same-site cross-origin frontends must be explicitly
  allowlisted. Truly cross-site frontend deployments are not supported by this
  cookie policy. Non-browser clients without these headers can bootstrap.
- A database advisory transaction lock serializes guest issuance across workers.
  The rolling one-minute count uses the database wall clock after acquiring the
  lock, and counts **all** recently created auth sessions, including revoked ones.
  At the cap, new issuance returns 429 `RATE_LIMITED`, `Retry-After: 60`, and
  no cookie; valid-cookie reads continue. This is a global issuance cap, not a
  per-IP quota or general denial-of-service defense.
- Missing profile versions and database errors return sanitized 503
  `DATA_UNAVAILABLE`. Incomplete creation rolls back user/profile/session rows.
  A valid session with a missing profile fails closed rather than silently
  creating a replacement identity. Bootstrap error responses are also no-store.
- No TTL cleanup job, long-term guest retention policy, or user-data erasure job
  is added. Plan retention separately. Keep edge request-rate limits and
  monitoring in place; Origin/Sec-Fetch-Site are not bot authentication.

## Verification and release smoke

Use a disposable PostgreSQL instance only. The existing acceptance fixture creates
and drops unique `jobtology_acceptance_<uuid>` databases and runs existing migrations
inside each. Its database role must be able to create databases.

```sh
JOBTOLOGY_ACCEPTANCE_DATABASE_URL='postgresql+asyncpg://<test-role>:<test-password>@127.0.0.1:<test-port>/<test-db>' uv run pytest -q
uv run ruff check .
uv lock --check
uv build
```

Focused tests: `tests/test_guest_session.py`, `tests/test_guest_persistence.py`,
`tests/test_guest_failures.py`, and `tests/test_guest_configuration.py`. They cover
real PostgreSQL HTTP bootstrap, A/B profile isolation, profile-version refresh,
cookie expiry/revocation, CSRF, rollback, concurrent issuance, configuration,
and sanitized failures. Without the acceptance URL, database cases skip.

After an approved deployment, use two fresh browser cookie jars: each session GET
must be 200 with a different UUID; repeating it must preserve UUID/version.
Update only A's profile using its CSRF token and current version; B must remain
unchanged. A mutation without CSRF must be 403; a product GET without a cookie
must be 401. Check Secure/HttpOnly/SameSite/Path on the production cookie and
Google login 503. Do not log credentials while recording smoke results.

Rollback: set `JOBTOLOGY_GUEST_SESSIONS_ENABLED=false` while leaving
`JOBTOLOGY_AUTH_ENABLED=false`, then restart/redeploy. Default application
composition no longer accepts those sessions and cannot create new guests;
persisted rows remain. Re-enabling guest mode restores access for still-valid
unrevoked cookies. Permanent invalidation requires a separately authorized
session-revocation operation.

## Known non-auth work remaining

- The frontend's `version` versus backend `profile_version` mismatch is not fixed.
  Bootstrap returns the backend's real `profile_version`; no alias is invented.
- Occupation/catalog data readiness and analysis/roadmap content availability are
  independent of authentication. No fake occupation catalog or fixture data is
  introduced to conceal missing data.
- Frontend code is unchanged. Successful backend authentication alone does not
  establish that the full frontend product flow works.
- Google sign-in, account linking/recovery, and production rollout remain separate
  work requiring explicit review and operational approval.
