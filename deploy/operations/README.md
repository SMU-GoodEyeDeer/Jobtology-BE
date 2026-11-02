# Private management network on Goldship

The separate Coolify Railpack applications need a persistent private connection
for the admin dashboard's `/api/application/*` proxy. The dashboard uses
`http://jobtology-management-api:8000`; this alias must exist on the dedicated
internal `jobtology-management` bridge. A manual network attachment disappears
when Coolify replaces a container. Coolify's Custom Docker Options does not apply
`--network`.

`management_network.py` runs **on the Docker host**, under its existing Docker
operator. It attaches healthy containers from these two Coolify resources only:

| Resource | Coolify ID | Private alias |
| --- | --- | --- |
| Backend | `w131qqclc6wyke8347y9puqo` | `jobtology-management-api` |
| Admin dashboard | `d0n6yjniwrcfoz0oppkjhjua` | `jobtology-management-dashboard` |

It requires Linux, Python 3 and Docker with `network connect --gw-priority`
support (API 1.48+, verified with Engine 29.8.2 on Goldship). It uses the local
Unix socket explicitly and ignores `DOCKER_*` environment overrides. Never
mount that socket in either application.

The bridge has `Internal: true`. Existing `coolify` attachments and outbound
routes stay in place: the new attachment has gateway priority `-1`. Traefik must
also be explicitly pinned to `coolify` for public ingress. Gateway priority does
not control which container address Traefik selects.
The helper refuses non-internal networks, unapproved members, and existing
attachments with unexpected aliases or gateway priorities. It never removes
containers or disconnects networks, and does not inspect credentials or
container commands. Docker operators and the two application containers remain
trusted.

## Install or update

Run these commands as `maxjo` **on Goldship**, from the matching backend checkout.
They need no Python packages, database credentials or management token:

**Before attaching either application**, preserve its existing Coolify container
labels and add:

```text
traefik.docker.network=coolify
```

Apply the labels with a same-version Coolify restart and verify the label on
each running container. Saving the setting alone does not update an existing
container. Without this pin, Traefik can select the private management address,
which its proxy cannot reach; the whole public page then times out even while
container health and private API requests succeed. The helper refuses to add
attachments to Traefik-exposed containers without this pin.

```bash
install -d -m 700 "$HOME/.local/libexec"
install -m 700 deploy/operations/management_network.py \
  "$HOME/.local/libexec/jobtology-management-network.py"
install -d -m 700 "$HOME/.local/state/jobtology-management-network"
/usr/bin/python3 "$HOME/.local/libexec/jobtology-management-network.py"
```

The last command should report zero attachments when everything is already
connected. It waits without mutations if either resource has no healthy instance.
For a new topology, healthy instances are attached automatically.

Preserve existing user cron jobs. Add this single line to `crontab -e` (the
paths below are for Goldship's `maxjo` user):

```cron
* * * * * /usr/bin/python3 /home/maxjo/.local/libexec/jobtology-management-network.py --watch --state-dir /home/maxjo/.local/state/jobtology-management-network >/dev/null 2>&1 # jobtology-management-network
```

Cron starts the watcher within a minute of boot or an unexpected exit. A private
file lock prevents duplicate watchers. The running watcher checks every ten
seconds, so management requests can briefly return 503 while replacement
containers become healthy and acquire the attachment. Changes are logged to
`~/.local/state/jobtology-management-network/watch.log`, with bounded rotation.
The installed copy is independent of the checkout: after updating it, terminate
the PID recorded in `watch.lock` after verifying it belongs to this helper.
Cron starts the updated copy; no application redeploy is needed.

On November 2, 2026, the helper and this cron job were installed on Goldship.
An initial installation checked private API connectivity but missed Traefik's
network selection, causing the public admin page to time out. The repair pinned
both applications to `coolify`, replaced their containers with the same deployed
versions, and added the helper's ingress preflight. Only the backend and admin
dashboard were attached. Verification covers public pages and session/readiness
endpoints as well as the dashboard runtime client's invitations, users and
sessions requests. Disposable Docker fixtures verified replacement attachment,
idempotency, gateway preservation and rejection of an unrelated network member.

## Verify and remove

```bash
docker network inspect jobtology-management \
  --format '{{.Internal}} {{range .Containers}}{{.Name}} {{end}}'
tail -n 20 "$HOME/.local/state/jobtology-management-network/watch.log"
```

The first output must show `true` and only instances of the two resource IDs
above. Verify both containers still have their original `coolify` attachment,
and the `traefik.docker.network=coolify` label. Check the public admin page and
`/api/session`, backend readiness, and the admin Invitations, Users and Sessions
pages after a redeploy. Private API success alone does not establish ingress
health.
Keep the management API token in backend/dashboard Runtime secrets only.

To disable automation, remove only the marked line from `crontab -e`, then
terminate the verified helper PID from `watch.lock`. Existing attachments remain
usable until container replacement. Do not disconnect the live network as part
of disabling the watcher. When changing resource IDs, review the allowlist and
network membership explicitly; the helper deliberately refuses unknown members.
