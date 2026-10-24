# Evidence-backed chat workspace

The `/chat` workspace combines account-scoped application reads, catalog search and
bounded graph traversal. It does not execute model-generated SQL/Cypher or change
profile, goal, competency credit, analysis, roadmap or catalog records. The graph
shows retrieved evidence and short explanations, not an internal model reasoning trace.

## HTTP contracts

- `GET /api/v1/chat/context?goal_id=<uuid>`: authenticated personal evidence graph.
  Omitting `goal_id` deliberately requests general account context; it does not
  select an arbitrary active goal. A foreign/missing goal returns 404 before reads.
  Responses use `Cache-Control: no-store` and the existing account chat quota.
- `POST /api/v1/chat/catalog`: `{q, offset}` or `{node_id, offset}`. Requires an
  authenticated session, CSRF and the existing account chat quota. `q` is 2–120
  characters; the shared page cursor advances by 24. Expansion is one hop per
  request, with explicit paging; it does not recursively traverse the database.
- `POST /api/v1/chat/stream`: `{messages, goal_id, focus_node_ids}`. Counseling
  requests only; the original `/chat/messages` onboarding/spec modes remain.
  Accepts at most 20 messages of 2,000 characters and eight bounded focus IDs.
  Ownership/initial read failures use ordinary HTTP statuses before streaming.
  Send `Accept: text/event-stream` for POST SSE with `evidence`, `progress`, `tool`,
  `answer_delta`, `answer`, `done` or safe `error` events. SSE uses matching `event`
  names and JSON `data`, with a comment heartbeat every ten idle seconds.
  `answer_delta` previews only the answer's reply text; citations and graph actions
  arrive in the fully validated `answer`. Interrupted previews remain marked
  incomplete and are excluded from subsequent conversation context. EOF without
  `done` is an interruption. The default remains NDJSON for older clients and omits
  the new tool/text events. Both formats use `no-store` and `X-Accel-Buffering: no`.
  Once the reply's JSON string is complete, `progress.stage=finalizing` identifies
  the remaining citation/graph generation. The UI keeps a visible processing status
  until `done` and successful stream parsing, then commits the final graph actions.

## Retrieval and provenance

Personal retrieval reads the authenticated account's profile, goals, route preferences,
active specs and their current deterministic matching results. The selected goal scopes
the dashboard analysis and active roadmap. Analysis profile/release versions, partial
credit, step states, prerequisites, solver dates and saved custom weekly dates are retained.
Reading saved schedule overrides does not run a solver or repack the plan. An outdated analysis
is explicitly flagged. Personal evidence is request-only and never enters the public
catalog cache. Active browser conversation/search state is memory-only and clears on goal
change/navigation/logout (at most 40 visible conversation messages). Initial evidence
graphs can be reused for 45 seconds across page visits, with at most six goals in
memory, keyed by account, selected goal and session profile version. Logout, account/
session changes, successful profile/spec/goal/preference/roadmap writes, analysis
requests/results and manual refresh invalidate these snapshots. Generation checks
reject late writes after invalidation. No browser storage or conversation caching is
used for these snapshots. Renderer code preloads alongside the initial reads.
Goal selection is
shared in memory across application pages and isolated by account. Goal-scoped analysis,
roadmap and profile links also retain their goal in the URL for reloads/new tabs.

Shared retrieval searches the whole available native NCS/O*NET catalog, qualifications,
exam sessions and reviewed publication metadata, rather than the initial role graph's
small samples. It also searches the approved skill aliases/Korean O*NET labels and
approved learning-activity templates across roles, plus the existing live posting feed
(including its Jobkorea/ALIO source policy). Approved template outcomes become explicit
learning-to-competency links pinned to the approved release. These are planning
references; they do not make an activity a hiring requirement.

Fixed parameterized Neo4j read definitions are registered in the existing Query API
allowlist. Public neighbors use an explicit relationship allowlist; only accepted
reviewed alignments are expanded. Private/unknown labels, stale O*NET occupations,
arbitrary URLs, writes, model-generated queries and external web search are excluded.
Posting search exposes the feed's available metadata; missing full duties or unmodeled
prices must not be invented. O*NET reference associations never imply NCS equivalence.

The model uses native Responses function calls in a bounded loop. Registered read
tools are `read_personal_records`, `search_catalog`, `search_postings`, `get_posting`
and `expand_connections`. Tool results can inform the next read. Account and goal
scope are server-owned; tools cannot supply account IDs. Detail/expansion requires
retrieved node IDs, and posting occupation filters require a retrieved occupation.
Arguments use strict schemas, an allowlisted dispatcher and no generated queries.
The final model call produces one structured answer. Source, node and edge IDs
are checked against the bounded evidence ledger. A selected public graph node can be
resolved through the same catalog allowlist. Suggested learning links are separately
validated and always marked `PROPOSED`/`SUGGESTED_LINK`; they do not alter assessment.
The UI can add/highlight/focus/filter/expand evidence and replay a cited answer's graph.

Posting groups expand into actual posting nodes. Keyword hits attach to a search
context with `SEARCH_RESULT` edges, rather than pretending to prove occupational
requirements. Occupation associations retain their original association ID, source
and catalog pins, review state and separate freshness flags. Historical groups are
visible as historical; their current-evidence weight remains zero. The frontend
initially expands one posting group, preferring an available O*NET group, and offers
the remaining groups through normal expansion. Closed status and deadlines belong
to the posting itself and are separate from association freshness. See
[the NCS revalidation report](chat-posting-revalidation-20261024.md).

## Bounds and failure behavior

- Personal retrieval has a 12-second deadline; relevance selection retains at most
  12 active specs, 20 analysis requirements, 16 roadmap steps and six goal summaries
  plus the explicitly selected goal.
- Each native search/expansion page retains at most 24 rows, plus a look-ahead row.
  Search enriches at most two hits with one-hop neighbors (two-second deadlines).
  Additional sources use smaller page sizes mapped to the shared cursor.
- The cumulative graph retains at most 160 nodes/320 edges. Initial personal evidence
  and account/goal roots are protected; older catalog nodes are evicted so new searches continue to work.
  Personal relationships are protected within the edge bound; other edges keep the
  newest retrievals and refreshed relationships.
  The UI discloses a partial view and supports reset/paging.
- Native retrieval defaults to three model rounds and six tool calls total, with
  at most three reads in flight. Each tool has a seven-second deadline. Each
  retrieval model round has an 18-second deadline; answering has 40 seconds; the overall
  stream has 75 seconds after initial reads. Four streams per API process are allowed,
  with a two-second capacity wait. The existing PostgreSQL per-account quota applies
  across workers. There are no automatic model retries.
- Only public native reads are cached (30-second TTL, at most 64 keys). Personal
  records and model answers are not cached or logged. Independent source failures
  preserve successful evidence and disclose warnings. Tool start/completion/failure
  status is streamed without raw arguments, credentials or internal reasoning.
  Cancellation closes work and
  releases the stream slot; previously accepted provider work may already be billed.

## Frontend and deployment

The graph uses Neo4j NVL 2 with a lazily loaded Canvas renderer, custom safe text labels,
node inspector, legend, keyboard-accessible node list, filters and camera controls.
Chat floats beside the graph with width adjustment/minimize/reopen; mobile uses a bottom
sheet. Container resize re-fits a bounded view; ordinary graph updates retain the NVL
instance and existing positions/camera. Reduced motion uses fixed positions and disables
UI/fit animations. NVL telemetry is disabled. Patched `js-cookie`/`fast-uri` overrides
keep the dependency audit clean.

Deploy the matching BE before the FE. No application migration, catalog SQL migration,
new credentials or model/provider change is required. The existing configured LLM is
used with structured outputs and `store:false`; this does not change provider retention
terms. The existing Neo4j Query API and live catalog connections remain server-side.

Optional server settings (no changes required for the default behavior):

| Environment variable | Default | Allowed values |
| --- | --- | --- |
| `JOBTOLOGY_CHAT_TOOL_CHOICE` | `auto` | `auto`, `required`, `none` |
| `JOBTOLOGY_CHAT_TOOL_MAX_ROUNDS` | `3` | 1–4 |
| `JOBTOLOGY_CHAT_TOOL_MAX_CALLS` | `6` | 1–12 |
| `JOBTOLOGY_CHAT_PARALLEL_TOOL_CALLS` | `true` | boolean |

`required` forces tool use during each retrieval round; the answer call always has
tools disabled. `none` skips retrieval calls and answers using initial evidence.
There are no model-generated writes or external web tools. Existing onboarding,
spec and résumé workflows retain their `complete_json` path.

Literal catalog name/code search is bounded by the Query API transaction deadline;
large production catalogs should be profiled before introducing a full-text index.
The NVL chunk is larger than the previous renderer and loads only on the graph page.

## Validation

`tests/test_chat_workspace.py` covers account/goal scoping, modeled partial credit,
stale analysis, stored schedule/dependencies, invented IDs, proposed links, partial
failure, stream cancellation and ownership before headers. Native-tool tests cover
iterative/parallel reads, budgets, principal scoping, duplicate call IDs and idle
heartbeats. Provider SSE tests cover both clients, split Unicode and incomplete/error
responses. Posting tests cover group expansion, search edges and preserved stale pins.
Existing chat tests protect
onboarding/spec behavior and quotas. FE tests cover stream framing/validation, graph
bounds, selected-goal propagation, stale-response cancellation and citation replay.

Optional real Cypher checks use a disposable local Neo4j only:

```sh
JOBTOLOGY_CHAT_NEO4J_TEST_URL=http://127.0.0.1:27474/db/neo4j/query/v2 \
  .venv/bin/python -m pytest tests/test_chat_catalog_neo4j.py
```

That fixture seeds and removes unique synthetic nodes and rejects non-loopback URLs.
Browser QA uses local synthetic API fixtures; it makes no provider requests. A bounded
live smoke test on 2026-10-24 used the existing `chatgpt_oauth` / `gpt-5.6-luna`
configuration, public catalog records and a synthetic account graph. It completed a
native posting-group expansion (six nodes) and incremental answer stream with a matching
validated final reply. No real résumé or private profile was submitted. Broad model
answer quality and production-scale search latency still need operational evaluation.
