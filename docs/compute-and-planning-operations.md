# Analysis budgets and reviewed roadmap revisions

This follow-up changes application behavior; it does not change the running
Coolify resources or reclassify catalogue evidence.

## Release order

1. Apply application migration `20261020_02` using the migration/admin role,
   through the same Goldship connection used for the previous upgrade:
   `uv run alembic upgrade head`. The restricted runtime role remains unchanged.
   This adds optional availability arrays to existing preferences with empty
   defaults; it preserves accounts, capabilities and roadmaps.
2. Deploy the matching BE and FE feature changes. Keep the readiness endpoint
   `/api/v1/health/ready`. The image now installs `libseccomp2` for the parser.
3. Set resource limits in Coolify and redeploy. A starting allocation for this
   small installation is **2 CPU cores and 2 GiB memory** with a process limit of
   256 where supported. This is an initial allocation, not a load-test result;
   monitor readiness latency and container OOM events before reducing memory.
   The application cannot enforce the host/container CPU or memory allocation.

Verify the image and limits without printing secrets:

```sh
docker inspect --format 'image={{.Config.Image}} user={{.Config.User}} memory={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}} cpu_quota={{.HostConfig.CpuQuota}} cpu_period={{.HostConfig.CpuPeriod}} pids={{.HostConfig.PidsLimit}} readonly={{.HostConfig.ReadonlyRootfs}}' CONTAINER_NAME
```

An empty user means the image still runs as root. Nonroot/read-only deployment
needs an image-level converter/runtime smoke check and writable private `/tmp`;
this release does not claim those controls or a separate parser container.
Do not enable privileged mode, add a Docker socket, or disable Docker's seccomp
profile to run document processing.

An isolated smoke check of the previously deployed runtime image passed Java and
.NET startup and synthetic PDF/DOCX extraction with the new network filter,
UID/GID 65532, read-only root, dropped capabilities, no-new-privileges, disabled
container networking, 512 MiB private `/tmp`, 2 CPUs and 2 GiB memory. This is
parser/runtime evidence only; it does not prove the newly built image's complete
authenticated application flow under those container options. Repeat the smoke
with the release image before adopting nonroot/read-only options in Coolify.

## Per-account compute limits

| Runtime setting | Default | Scope |
| --- | --- | --- |
| `JOBTOLOGY_ANALYSIS_REQUESTS_PER_HOUR` | 10 | New manually requested analyses |
| `JOBTOLOGY_ANALYSIS_EXECUTIONS_PER_HOUR` | 30 | All solver jobs, including automatic jobs from edits/completions |
| `JOBTOLOGY_ANALYSIS_EXECUTIONS_PER_MINUTE` | 2 | All solver jobs |
| `JOBTOLOGY_SOLVER_WORKER_COUNT` | 2 | Native solver threads per solve |
| `JOBTOLOGY_SOLVER_TIME_LIMIT_SECONDS` | 10 | Existing bound per solve |

Counters use PostgreSQL's clock and shared account rows, so logout, a new browser,
multiple API replicas and restarts do not reset them. They use fixed minute/hour
windows, not a sliding cooldown; requests around a boundary can form a short
burst. Only one valid solver lease per account may run at a time. A crashed
worker is eligible for recovery when its five-minute lease expires.

Matching pending/running work, matching completed results and HTTP idempotency
response replays do not consume the manual budget. Explicitly refreshing a
completed result consumes a new request. Rejection is `429 ANALYSIS_RATE_LIMITED`
with `Retry-After` seconds; it rolls back the request and counter reservation.

Automatic edits still save when compute capacity is exhausted. Their job stays
`PENDING`, with `outbox_jobs.available_at` moved to the next eligible window;
no failure or completed result is invented. Recompute polling exposes
`next_attempt_at` as an earliest retry time, not a guaranteed start. The FE keeps
that state visibly queued and polls less often. Superseded profile jobs are still
retired before execution. Watch `recompute_budget_deferred` logs and pending age
when adjusting the settings. API and external worker processes must use the same
budget settings.

The résumé child and its descendants receive a native seccomp filter before
opening the uploaded document: only Unix-domain sockets are allowed, io_uring
setup is denied to prevent asynchronous socket creation, and new exec privileges
are disabled. Parsing fails closed if the filter cannot be installed. Existing
CPU/file/descriptor/address-space and wall-clock limits remain. This protects
network access; it is not filesystem isolation or a memory cgroup. Keep the
existing no-secret child environment and private request directories.

## Scheduling and adoption

Route preferences optionally accept:

```json
{
  "weekly_availability": [{"weekday": 1, "start_hour": 19, "end_hour": 21}],
  "unavailable_dates": ["2026-12-25"]
}
```

Weekday 1 is Monday; hours are whole local hours in the goal's time zone. Split
overnight windows across days. Overlaps, invalid dates and reversed ranges are
rejected. Empty windows retain flexible weekly allocation; exclusions still
apply. The weekly hour budget remains an upper bound even with larger windows.
Windows/exclusions are pinned in analysis and proposal constraints, carried
forward on edits, and used during later rescheduling. Completed schedule history
is immutable. Public holidays and exam registrations are not automatically
excluded or prerequisites unless explicitly modeled.

Existing roadmaps stay unchanged after an analysis finishes. The roadmap page
offers a read-only comparison against the newest current proposal, followed by
explicit apply. Apply checks profile, roadmap, weekly-plan versions and the exact
proposal hash again in one transaction. It preserves verified completed history
and original completion lineage without minting credit, retains semantically
unchanged in-progress steps, and discloses in-progress steps absent from the new
route. Failed apply leaves the old active roadmap intact. Concurrent changes
require a new comparison. Weekly custom overrides are replaced by the new
proposal's schedule; completed snapshots are carried forward.

Roadmap skill descriptions use the full origin analysis, including satisfied
requirements, rather than the latest dashboard's missing-skill list. A failed
plan, proposal status or pinned metadata fetch shows retry UI.

## Separate O*NET context and catalogue refresh

`config/product_roles/posting_context.v1.json` supplies broad occupation filters
only for `/api/v2/roles/{role}/posting-context`. These application selections may
overlap across roles; they are not exact NCS/O*NET equivalents, reviewed employer
requirements or additional competency credit. Official reference occupations are
[Software Developers](https://www.onetonline.org/link/summary/15-1252.00),
[Web Developers](https://www.onetonline.org/link/summary/15-1254.00), and
[Data Scientists](https://www.onetonline.org/link/summary/15-2051.00).
The approved competency artifact and its digest are unchanged. SQL 031/032 need
no replacement for these additional context filters.

NCS catalogue freshness remains tied to the original publication pin. A new
READY catalogue run can make links stale even if some contents are unchanged.
Do not rewrite frozen pins, confidence or review status to make the counts look
current. Deterministic revalidation must compare archived and current canonical
target hashes, source hierarchy, labels and retrieval context separately, and
report changed/missing/unverifiable targets before any new publication is
considered. Full rematching or paid inference requires a separate bounded run.
This release neither publishes new NCS links nor claims the historical NCS
associations have been revalidated.
