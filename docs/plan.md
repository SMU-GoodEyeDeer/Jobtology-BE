# Jobtology v2 Architecture and Product Requirements

## Status and document authority

The current worktree implements the local-JSON M1–M5 product flow and the additive, read-only
native `/api/v2` catalog. Remote deployment, Google sign-in, remote publication/revocation
integration, and native editorial analysis or route planning remain pending or intentionally
unsupported. This document defines the current architecture and product requirements; it is not a
remote-release certification.

- The served `/api/openapi.json` is the field-level contract for an implemented deployment.
- [The Korean frontend integration guide](fe-integration.md) is the single wheel-backed source for
  `/api/guide`.
- [The native Neo4j source contract](neo4j-source-contract.md) defines observed source facts and
  privacy boundaries.
- [The verification record](neo4j-verification.md) preserves dated historical QA evidence; it is
  not a substitute for a new release gate.

## Product objective and scope

Jobtology helps a user turn current experience into an executable preparation plan:

```text
current experience → requirement gaps → next action → completed artifact
                   → updated readiness → next action
```

The outcome is preparation readiness by a target date, not a score that promises employment.
Every recommendation must expose why it exists, its completion criteria, estimated effort, and
the evidence or trace that supports it. AI chat, interview practice, resume analysis, and
AI-written cover letters are out of scope until separately specified.

## Architecture and ownership

```text
frontend → same-origin /api/v1 → FastAPI modular monolith → application PostgreSQL
                                      │                         │
                                      │                         └→ outbox → worker → analysis / solver
                                      │
                                      └→ /api/v2 → explicitly configured Neo4j native catalog
```

- API and worker are separate processes from one codebase. Asynchronous I/O stays separate from
  CPU-bound planning work.
- The application database owns users, profiles, goals, preferences, analyses, proposals,
  roadmaps, events, idempotency records, and the application outbox.
- A corpus provider owns ingestion, normalization, publication, and source evidence. The backend
  consumes a version-pinned, read-only contract; it must not query ingestion internals, import
  pipeline modules, or create a distributed transaction with corpus infrastructure.
- Corpus-backed output retains the source/basis/release metadata and evidence needed to explain
  it. A revoked or otherwise unusable basis cannot be silently reused.

## API and source boundaries

### Product API (`/api/v1`)

The product API manages user-owned state and the asynchronous analysis-to-roadmap flow. Mutations
use optimistic versions and, where documented, `Idempotency-Key`. A standard error response is:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": [{"location": ["body", "field"], "code": "missing"}],
    "request_id": "..."
  }
}
```

The current authentication posture is fail-closed: with
`JOBTOLOGY_AUTH_ENABLED=false`, product requests require no fallback identity and return
`401 UNAUTHENTICATED`. Fixtures are explicit development-only examples; they never authenticate
or substitute for persisted product behavior.

### Native catalog API (`/api/v2`)

`/api/v2` is a separately configured, read-only native catalog contract. It is additive to v1 and
does not translate native IDs into v1 editorial IDs, basis versions, or releases.

- Supported catalog reads are occupations, publications, and publication alignments, each with
  offset pagination (`limit` 1–100, default 100; `offset` >= 0, default 0).
- `READY` is a verbatim native source state; it is **not** the application's editorial
  `PUBLISHED` state or proof that analysis is available.
- An `accepted` native alignment is **not** a required competency, user capability, satisfaction
  result, coverage score, or route-planning outcome.
- `source_current=false` records source history only. It does not mean deleted, revoked, or
  invalid unless a separately verified source rule says so.
- The native catalog can be supported while native editorial analysis and native route planning
  remain intentionally unsupported. Missing reviewed requirements or activity templates must fail
  closed rather than produce a `READY` analysis or proposal.
- Native public projections are an allowlist. Raw payloads, reviewer/candidate fields,
  `decision_id`, source-provenance identifiers, and arbitrary graph properties remain private.

The default local JSON source and a native catalog source are distinct modes. There is no implicit
fallback from a failed native source to local JSON, and persisted source selections must remain
interpretable independently of a later default change.

## Product requirements

| Area | Required behavior |
| --- | --- |
| Profile and constraints | Preserve raw capability input while resolving canonical entities where possible. Treat unresolved or ambiguous input as no coverage credit. Validate dates, weekly capacity, budgets, and time zones at their owning boundary rather than in capability normalization. |
| Goals | Support targeted and discovery goals. A discovery result never creates a roadmap until the user explicitly selects a target. |
| Analysis | Separate `REQUIRED` and `PREFERRED` coverage. Distinguish `SATISFIED`, `UNMET`, and `NEEDS_INPUT`; missing evidence is not a supported zero. EDITORIAL coverage is not MARKET fit. |
| Candidate and route planning | Expand unmet requirements with prerequisite-aware activities. Enforce dependencies, capacity, hard budget, calendar, and deadline constraints. Report infeasible or timeout states explicitly; never invent effort, cost, evidence, or an arbitrary compressed route. |
| Roadmaps | Create a `DRAFT` first and activate only through an explicit user action. Keep one active roadmap per user, version-check changes, and retain the proposal/basis metadata that justified it. |
| Step completion | Allow only valid state transitions on active roadmaps. Completion creates labeled, event-derived outcomes; reversal removes only the outcome lineage created by that completion. Planned outcomes do not count as current capability. |
| Recompute | Mutations enqueue version-pinned work. Stale work cannot replace current analysis, and a recompute never silently rewrites a saved roadmap. |
| Dashboard and next actions | Compose current analysis, active roadmap, recent events, attention items, and eligible actions without inventing state. Exclude blocked, expired, completed, inactive, or invalid work from continuation recommendations. |
| Progress, comparison, and discovery | Keep execution progress separate from readiness. Compare scores only when occupation, basis, release, methodology, and denominator are comparable; otherwise return a baseline-change explanation. |
| Evidence and privacy | Every user-visible derived result must retain appropriate source/evidence/trace references. Enforce user ownership and do not expose raw native source content or private review data. |

## Data and lifecycle invariants

- Primary application records use stable IDs; mutable user operations compare the current version
  and advance it transactionally.
- Analyses, proposals, calculation traces, and recompute contexts capture the input version,
  reference time, methodology, and source selection needed for replayability.
- An empty or insufficient baseline is an explicit unavailable/insufficient-data state, not a
  fabricated readiness value.
- Roadmap execution progress is not requirement coverage. A completed actionable-step fraction,
  a requirement-satisfaction score, and a market signal remain separate metrics.
- A source publication, a local release, and a user roadmap are different identities. Do not infer
  one from another.

## Operational requirements

- Production configuration uses explicit origins, rejects fixtures and mock samples, and treats
  `/api/v1/health/live` as process liveness rather than database readiness.
- Database migration is a one-off release action before traffic promotion, not an action run by
  every API replica. A worker runs as a separately scheduled one-shot batch process.
- Deploy only a commit that passed its matching checks. Retain the previous image and use
  backward-compatible migrations to keep rollback possible.
- The [deployment runbook](../deploy/README.md) is manual-only. It does not authorize a team
  member to deploy, alter remote infrastructure, or expose runtime secrets.

## Completion evidence

Feature completion requires current, reproducible tests, lint, lockfile verification, and a
package build. PostgreSQL acceptance checks require a newly provisioned disposable database and
must not be reported as rerun when that environment is absent. The dated `306`-test native/local
verification and its unclassified dashboard-flake note remain in
[neo4j-verification.md](neo4j-verification.md) for historical context only.
