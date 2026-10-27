# Google browser sign-in

Google OAuth is the only supported sign-in method. Login is disabled until credentials
are configured; disabled authentication never creates anonymous users. To enable it,
create a **Web application** OAuth client in Google Cloud Console (configure the consent
screen and test users while the app is in Testing). Register this exact authorized redirect URI:

`https://jobtology.yeongmin.net/api/v1/auth/google/callback`

Configure the backend securely in the deployment environment; do not put client secrets in source control or frontend variables:

| Variable | Production value |
| --- | --- |
| `JOBTOLOGY_AUTH_ENABLED` | `true` only after all other values and database migrations are ready |
| `JOBTOLOGY_GUEST_SESSIONS_ENABLED` | `false`; guest authentication has been removed |
| `JOBTOLOGY_GOOGLE_CLIENT_ID` | Web application client ID from Google |
| `JOBTOLOGY_GOOGLE_CLIENT_SECRET` | Matching Web application client secret (secret variable) |
| `JOBTOLOGY_GOOGLE_REDIRECT_URI` | `https://jobtology.yeongmin.net/api/v1/auth/google/callback` |
| `JOBTOLOGY_FRONTEND_URL` | `https://jobtology.yeongmin.net` |
| `JOBTOLOGY_CORS_ORIGINS` | `["https://jobtology.yeongmin.net"]` |
| `JOBTOLOGY_DATABASE_URL` | Application PostgreSQL connection with the existing auth migrations applied |

In Coolify, set these as literal Runtime variables, with Build time disabled. Keep the
client secret in the backend only. If an Authorized JavaScript origin is requested,
use `https://jobtology.yeongmin.net`; this flow uses the backend callback rather than
an in-browser token exchange. Stage the URLs while `JOBTOLOGY_AUTH_ENABLED=false`,
add the matching client ID and secret, then set authentication to `true` and redeploy.

The application identifies accounts by Google's issuer and immutable subject, not email.
Existing guests are not converted or merged. Sessions without a Google identity are rejected.
Chat allows 20 requests per account per fixed minute using shared PostgreSQL counters;
logging in again or opening another browser does not reset the same account's allowance.
Exceeded quotas return 429 with Retry-After. Database quota failures return 503.

Run Alembic through revision `20261019_01` before serving authenticated chat traffic.
The runtime role can be separated from migration/admin credentials using
[`deploy/restricted-application-role.sql`](../deploy/restricted-application-role.sql).

The browser completes Turnstile, then sends `POST /api/v1/auth/google/login` with
JSON `{"cf-turnstile-response":"<single-use token>"}` and credentials included.
The backend verifies the token before creating an OAuth attempt and returns an
`authorization_url`; the browser navigates to that Google URL. Google receives
`openid`, state, nonce and PKCE S256. After normal user consent, Google returns to
the exact backend callback. The backend exchanges the code with Google, validates
the signed ID token against Google's fixed JWKS, stores or resolves the user
solely by Google's immutable `sub`, issues a server-side session, and redirects to
the configured frontend origin. The frontend can then call `GET /api/v1/auth/session`
with credentials to obtain the CSRF token; send it in `X-CSRF-Token` for
state-changing calls and logout. The session and temporary login-binding cookies
are HttpOnly, Secure in production, and SameSite=Lax. The temporary binding expires
after ten minutes and is consumed once.

## Turnstile deployment

Use the existing widget `0x4AAAAAAFnjRx897yLX5iji`; do not create a replacement.
The frontend includes this public site key. `VITE_TURNSTILE_SITE_KEY` can override
it at build time for a separate development/test widget; never place a secret in
any `VITE_*` variable.

Before deploying the matching backend and frontend, add these backend Runtime
variables in Coolify with Build time disabled:

| Variable | Production value |
| --- | --- |
| `JOBTOLOGY_TURNSTILE_SECRET` | Existing widget's **Secret Key**, stored as a secret |
| `JOBTOLOGY_TURNSTILE_HOSTNAMES` | `["jobtology.yeongmin.net"]` |
| `JOBTOLOGY_TURNSTILE_ENABLED` | `true` (the default) |

The widget's allowed domains in Cloudflare must include `jobtology.yeongmin.net`.
Only the backend calls Siteverify, requiring `success === true`, action
`google_login`, and an exact allowlisted frontend hostname. The POST also requires
the configured frontend Origin. Rejected, expired, replayed, unavailable, or
malformed verification does not create an OAuth attempt. Tokens stay out of URLs,
and neither tokens nor the secret are logged. Turnstile is not required again on
the Google callback or for existing sessions. Direct GET login is rejected while
protection is enabled, so deploy both versions together.

For isolated local development, explicitly set `JOBTOLOGY_TURNSTILE_ENABLED=false`
or configure a development/test widget and local hostnames. Never include local
hosts in the production backend's allowlist. A missing secret/allowlist with
authentication and protection enabled fails configuration validation.

Validate the deployed destination with a fresh real browser token: the first
login POST should succeed and set the OAuth binding cookie; replaying that token
must fail. Then finish Google consent and check the session. Mocked tests verify
these contracts but do not prove the live widget or deployed secret is correct.

Do not use the OAuth client secret as a frontend secret, do not link users by email, and do not substitute test fixtures for a human Google consent check. Local tests use synthetic signed JWT keys and a fake HTTP transport only; they do not demonstrate live Google consent. Once the client has been provisioned, test the browser flow with an authorized human Google account, then verify `/api/v1/auth/session`, CSRF rejection, and logout. Keep `JOBTOLOGY_AUTH_ENABLED=false` until those prerequisites are ready; missing live client credentials are an external configuration blocker, not a reason to forge a session.

The application removes callback query parameters from its own access-log scope. Also ensure the external ingress/reverse proxy does not log OAuth callback query strings, which contain a one-time authorization code and state.

## Random token hashing and code scanning

Session and CSRF tokens, OAuth state, and the browser-binding token are generated
server-side with `secrets.token_urlsafe(32)` (256 random bits per token). Their
SHA-256 digests are stored for lookup; callback and cookie values are checked
against those existing digests. These values are not human-chosen passwords.
SHA-256 is appropriate for these high-entropy tokens; human passwords would need
a dedicated password KDF. See [Python's token generation documentation](https://docs.python.org/3.12/library/secrets.html#generating-tokens)
and [CodeQL's hashing guidance](https://codeql.github.com/codeql-query-help/python/py-weak-sensitive-data-hashing/).

CodeQL alert #1 (`py/weak-sensitive-data-hashing`) labels the OAuth callback state
as a password. Review that finding as a false positive for this verified token
flow; keep scanning enabled. Changing these digests without a compatibility plan
would invalidate stored sessions and pending OAuth attempts. No session hash key
is required. Regression tests cover existing SHA-256 sessions, pending OAuth
callbacks, and the token sizes on both issuance paths.
