# Guest sessions removed

Guest authentication and anonymous account issuance were removed on October 19, 2026.
Google OAuth is the only supported sign-in method. The deprecated
`JOBTOLOGY_GUEST_SESSIONS_ENABLED` setting accepts only `false`; `true` fails startup.

`GET /api/v1/auth/session` never creates an account. It requires an unexpired,
unrevoked session belonging to an ACTIVE user with a canonical Google identity.
Legacy guest sessions are rejected even if their cookies have not expired.
Logout revokes the session and does not create a replacement user.

Shared `users`, `profiles`, `auth_sessions`, and product tables remain because Google
accounts use them. There were no separate guest-only tables. The one-time production
cleanup removes accounts without Google identities and their dependent product data;
it preserves Google accounts, application migrations, LLM credentials, and the source catalog.

See [Google sign-in configuration](google-auth.md) for the OAuth variables and callback.
