# Google browser sign-in

Google login is disabled by default. To enable it, create a **Web application** OAuth client in Google Cloud Console (configure the OAuth consent screen and its authorized users as required). Register this exact authorized redirect URI:

`https://jobtology.yeongmin.net/api/v1/auth/google/callback`

Configure the backend securely in the deployment environment; do not put client secrets in source control or frontend variables:

| Variable | Production value |
| --- | --- |
| `JOBTOLOGY_AUTH_ENABLED` | `true` only after all other values and database migrations are ready |
| `JOBTOLOGY_GOOGLE_CLIENT_ID` | Web application client ID from Google |
| `JOBTOLOGY_GOOGLE_CLIENT_SECRET` | Matching Web application client secret (secret variable) |
| `JOBTOLOGY_GOOGLE_REDIRECT_URI` | `https://jobtology.yeongmin.net/api/v1/auth/google/callback` |
| `JOBTOLOGY_FRONTEND_URL` | The trusted absolute HTTPS frontend origin (no path, query, or fragment) |
| `JOBTOLOGY_CORS_ORIGINS` | Include exactly the trusted frontend origin |
| `JOBTOLOGY_DATABASE_URL` | Application PostgreSQL connection with the existing auth migrations applied |

The browser navigates to `GET https://jobtology.yeongmin.net/api/v1/auth/google/login` (not an AJAX token request). The backend redirects to Google with `openid`, state, nonce and PKCE S256. After normal user consent, Google returns to the exact backend callback. The backend exchanges the code with Google, validates the signed ID token against Google's fixed JWKS, stores or resolves the user solely by Google's immutable `sub`, issues a server-side session, and redirects to the configured frontend origin. The frontend can then call `GET /api/v1/auth/session` with credentials to obtain the CSRF token; send it in `X-CSRF-Token` for state-changing calls and logout. The session and temporary login-binding cookies are HttpOnly, Secure in production, and SameSite=Lax. The temporary binding expires after ten minutes and is consumed once.

Do not use the OAuth client secret as a frontend secret, do not link users by email, and do not substitute test fixtures for a human Google consent check. Local tests use synthetic signed JWT keys and a fake HTTP transport only; they do not demonstrate live Google consent. Once the client has been provisioned, test the browser flow with an authorized human Google account, then verify `/api/v1/auth/session`, CSRF rejection, and logout. Keep `JOBTOLOGY_AUTH_ENABLED=false` until those prerequisites are ready; missing live client credentials are an external configuration blocker, not a reason to forge a session.

The application removes callback query parameters from its own access-log scope. Also ensure the external ingress/reverse proxy does not log OAuth callback query strings, which contain a one-time authorization code and state.
