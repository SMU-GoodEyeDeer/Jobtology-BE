# Invite-only Google accounts

New Google accounts require an invitation by default (`JOBTOLOGY_INVITATION_REQUIRED=true`).
Migration `20261102_01` grants all Google accounts present at migration time unlimited
`LEGACY` admission. Existing sessions remain usable. New accounts authenticate with
Google before an invitation use is consumed. Google `iss` + `sub`, rather than a display
name or email address, identifies the account.

## Independent invitation settings

- **Code expiry** (`expires_at`): last instant for admitting a new account; null means no expiry.
- **Account access** (`access_hours`): fixed hours starting at first successful redemption;
  null means unlimited account access. Fractions such as `0.5` are supported. A new login
  or a second code cannot restart this window. An administrator can explicitly change
  an account's `access_expires_at` to extend it or set it to null for unlimited access.
- **Reuse** (`max_uses`): 1 for single-use, N for N distinct Google accounts, null for
  unlimited distinct accounts. Repeat logins of an admitted account do not consume uses.

Code revocation/expiry and edits affect future admissions. They do not suspend admitted
accounts or rewrite their original access windows. Suspend an account in Users to block
new logins and existing authenticated requests, and revoke its active sessions. Restoring
an account does not renew an expired access window or resurrect revoked sessions.
Browser sessions retain the configured session TTL and are capped at the account access
expiry. Access is checked again during session resolution and CSRF validation.

Admission and session creation commit together. A Google identity advisory lock and a
row lock on the invitation prevent duplicate redemptions and oversubscribed final slots.
Failed/cancelled OAuth, invalid invitations and database rollback do not consume a use.

Invitation codes have 120 random bits, are shown only at creation and are stored only as
hashes. Save the code/QR at creation, or create a replacement if lost. QR links use a URL
fragment (`/#invite=...`), which the FE removes from browser history after reading. The
code is submitted only in the login POST body, not sent to Google or stored in localStorage.
OAuth state, browser binding, nonce, PKCE and Turnstile checks remain enabled.

A minimal admission ledger persists after account deletion: an issuer/subject digest,
original admission source, expiry and invitation reference, without a raw Google subject,
name, profile, or session credential. Its user reference is set to null on erasure.
Recreating an account with that Google identity can restore only the remaining original
access; it does not restart a trial, consume a second invitation, or bypass revocation.
The privacy/retention policy should disclose this anti-abuse admission record.

## Deployment order

1. Pull this BE revision in **`~/Jobtology-BE` on Goldship**, or use the matching
   deployment image. Verify `.venv/bin/alembic -c pyproject.toml heads` reports
   `20261102_01` before touching the database. A single-branch clone may need:

   ```bash
   git remote set-branches --add origin feat/invitation-access
   git fetch origin
   git switch feat/invitation-access
   ```

   **Stop every old backend instance and block Google login and OAuth callback
   traffic before running the migration.** Keep the old processes stopped and
   callbacks blocked through the cutover. This includes in-flight callbacks:
   stopping only the FE login button does not prevent the old backend from
   creating a Google identity after the one-time LEGACY backfill. Stage the
   replacement image before this maintenance window; disable automatic
   deployment/restart of the old version until the cutover is complete.

   Run migration **before deploying the backend**, using the application migration/admin
   credentials, not the catalog DB URL:

   ```bash
   JOBTOLOGY_DATABASE_URL="$JOBTOLOGY_MIGRATION_URL" .venv/bin/alembic -c pyproject.toml upgrade head
   JOBTOLOGY_DATABASE_URL="$JOBTOLOGY_MIGRATION_URL" .venv/bin/alembic -c pyproject.toml current
   ```

   Head must be `20261102_01`. `JOBTOLOGY_MIGRATION_URL` above denotes a privately supplied
   URL; never paste secrets in chat, command history, source control or logs. A host needs
   a reachable private database address; container DNS names work only on its Docker network.
   Confirm the restricted application role can SELECT/INSERT/UPDATE `invitation_codes`,
   `account_access` and INSERT `management_audit`. Existing configured default grants
   normally cover the new tables. **Apply `deploy/invitation-access-grants.sql`
   even if those grants already exist**, as the application database owner,
   adapting the role name if needed. It explicitly removes DELETE on invitation
   codes/admissions and UPDATE/DELETE on the append-only audit records.

   If `20261102_01` was already applied while the old backend was running, keep
   the old instances stopped and callbacks blocked, then run
   **`deploy/reconcile-legacy-admissions.sql`** as the application database owner
   before reopening Google login. It adds only missing LEGACY admissions for
   the existing identity snapshot, never overwriting expiry, revocation or
   invitation usage. Its verification query must return
   `identities_without_admission = 0`. It is idempotent, but is a one-time
   cutover repair, not a startup hook or an open-registration fallback.
2. Generate an independent random management token (at least 32 characters) and store it
   as a secret backend Runtime variable `JOBTOLOGY_MANAGEMENT_TOKEN`. Set the same value
   in the **admin dashboard** Runtime secret `JOBTOLOGY_ADMIN_APPLICATION_API_TOKEN`.
   Do not use the Google client secret, Turnstile secret or migration credential.
3. Establish the management transport boundary before enabling the shared token.
   Choose either a dedicated internal network containing only the trusted backend
   and admin dashboard, or HTTPS with verified server identity (normal CA or an
   authenticated encrypted tunnel). Keep `/internal/admin` off public ingress.
   For the dedicated-network topology below, set dashboard
   `JOBTOLOGY_ADMIN_APPLICATION_API_URL=http://jobtology-management-api:8000`.
   The existing `http://jobtology-be:8000` alias on shared `coolify` is not the
   approved bearer-token transport. No management secret belongs in `VITE_*`.
4. Deploy matching BE, FE and Jobtology-DB dashboard revisions while the old
   instances remain stopped and callbacks blocked. Verify the new backend is
   ready at `/api/v1/health/ready`, uses the migrated application database and
   has invitation enforcement enabled. Only then reopen login/callback traffic.
   Do not restart the old backend against this cutover snapshot. Preserve existing Google
   and Turnstile settings. `JOBTOLOGY_INVITATION_REQUIRED=true` is the default; disabling
   it permits open registration and is intended only for isolated tests/development.
5. In the dashboard, create a test invitation, save its QR, and complete a real Google
   login. Verify one use, unchanged expiry on re-login, a rejected extra account for a
   single-use invitation, and immediate rejection after user/session revocation. These
   live Google/Turnstile checks require the deployed configuration; mocked/local tests
   do not establish them.

## Administration boundary

The shared `coolify` bridge also carries unrelated services. HTTP on that bridge
provides no encryption; its address being private does not establish a trusted
boundary for an administration bearer token. Before enabling application
management, apply one of these transport configurations:

- **Dedicated network:** attach only the approved backend and admin dashboard to
  the internal bridge `jobtology-management`; expose backend alias
  `jobtology-management-api` only on that bridge. Keep their existing `coolify`
  attachments for ingress, databases and outbound provider access. Use the
  management-only alias in the dashboard API URL so the bearer crosses the
  dedicated bridge rather than the shared one. The Docker host, its operators,
  and these two containers are trusted; do not attach other applications, the
  FE, databases or public proxies to this network.
- **Encrypted transport:** configure an HTTPS endpoint for the management path
  with normal certificate and hostname verification, or use an authenticated
  encrypted tunnel. Do not disable certificate verification. Keep the endpoint
  private or enforce an equivalent ingress restriction; TLS alone does not
  replace the management bearer authentication.

[`deploy/management-network.compose.yaml`](../deploy/management-network.compose.yaml)
records the dedicated network topology as a Compose override/reference. Merge it
with the actual approved service definitions and preserve existing images,
secrets, volumes and ingress rules. It does not deploy standalone Railpack
applications by itself. For the current separate Coolify applications, persist
these same attachments and the management-only alias in their resource/deployment
configuration. A one-off `docker network connect` is insufficient because
replacement containers lose manual attachments. Coolify's Custom Docker Options
does not apply `--network`; do not use that field as a persistence mechanism.

After deployment, verify `docker network inspect jobtology-management` lists only
the approved backend/dashboard containers and has `Internal: true`; confirm the
dashboard resolves `jobtology-management-api` on that network and reaches backend
health. Recheck membership/alias after replacing either container. The earlier
Goldship HTTP probe established shared-network reachability only; this dedicated
network or TLS configuration has not been applied to production by the PR.

References: [Docker networking](https://docs.docker.com/engine/network/),
[Coolify networking](https://coolify.io/docs/services/configuration/networking), and
[Coolify Custom Docker Options](https://coolify.io/docs/applications/builds/custom-docker-options).

The existing Jobtology-DB dashboard keeps its own login, CSRF checks and read-only
catalog telemetry credentials. Its server calls fixed `/internal/admin` endpoints with
an independent bearer token. These routes are excluded from public OpenAPI and normal
frontend proxy routes; bearer authentication is still mandatory. Unconfigured management
is disabled. Successful writes record action/target/time in `management_audit`; raw codes,
session/CSRF hashes and service credentials are never listed.

The new administration supports Invitations, Users and Sessions. Session lists contain
creation/expiry/revocation metadata, not raw credentials or exact presence tracking. The
existing bounded cleanup removes expired/revoked sessions during login; the list is not
an unlimited historical session archive.

For the existing dashboard's separate PostgreSQL credential failure and missing Neo4j
configuration, see `Jobtology-DB/docs/admin-dashboard.md`'s November 2 diagnosis. That
configuration repair is independent of application invitation migrations.
