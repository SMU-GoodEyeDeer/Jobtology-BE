# Jobtology Analysis and Roadmap Implementation Plan

Date: 2026-09-20 · Status: proposed design with an implemented scaffold

FSD means Functional Specification Document.

## 1. Goals and scope

Compare user-entered capabilities and experience with target-occupation requirements, select activities that address the gaps, and build an executable roadmap. Deliver the first vertical slice for BACKEND_DEVELOPER, then expand to AI_ENGINEER, FRONTEND_DEVELOPER, and DATA_ANALYST. AI chat is a later phase. The outcome is application readiness by a target date, not a score promising employment.

Initial acceptance criteria: four user types (beginner, programming fundamentals, existing project, and application-preparation stage) receive appropriate starting points and activities. Each step exposes its selection rationale, completion criteria, and estimated effort. Record inputs, versions, and the calculation reference time to support reproducibility and detect infeasible routes or constraint violations.

## 2. Agreed implementation units

| Unit | Responsibility | Input → output |
|---|---|---|
| ProfileNormalizer | Validate capability names and experience codes, resolve canonical capabilities, identify ambiguity | Capability input → normalized profile |
| GapAnalyzer | Evaluate satisfied, unmet, and clarification-required requirements | Profile + version-pinned requirements → analysis |
| CandidateBuilder | Expand activities and prerequisites addressing unmet requirements | Analysis + templates → candidate set |
| RoutePlanner | Select activities and solve dependency, calendar, capacity, and cost constraints | Candidates + PlanningConstraints → proposal |
| RoadmapService | Persist proposals and manage roadmap creation, activation, progress, and versions | User commands → saved roadmap |

**ProfileNormalizer excludes dates, time availability, and budgets.** The goals service interprets and validates target dates. PlanningConstraints and goal preferences validate available hours and budgets. RoutePlanner receives these values separately. Users can enter or update an integer weekly capacity from 1 to 60 through the survey without chat. A low-cost preference is distinct from a hard monetary cap.

The current Protocol definitions describe initial boundaries. M1 expands them with candidate costs, duration, calendars, and satisfaction rules. Scaffold DTOs are not final database models.

## 3. Architecture and ownership

```text
FE → same-origin /api/v1 → FastAPI → jobtology_app PostgreSQL
                              ├→ release-reader → pipeline publication views
                              └→ Neo4j corpus (release-scoped)
app transaction → outbox → worker → analysis / solver / Person projection
```

The backend is a modular monolith. API and worker run as separate processes from the same codebase. PostgreSQL is authoritative for user state. Neo4j Person nodes are rebuildable projections; analysis uses application profile snapshots rather than waiting for graph synchronization. Keep asynchronous API I/O separate from CPU-bound solver execution.

Jobtology-DB owns ingestion, normalization, evidence, publication of occupation requirements/statistics/templates, and the public Neo4j corpus schema. BE owns users, goals, plans, analyses, and application migrations. BE uses publication views and version-pinned JSON Schemas rather than querying ingestion internals or importing pipeline modules.

### Changes to coordinate with the existing DB plan

- Update mandatory sources, cohorts, freshness rules, and publication gates for the exclusion of Work24 and the inactive Saramin source. The previous eight-source requirement currently blocks publication.
- Introduce `basis_type=EDITORIAL|MARKET`. EDITORIAL reports completion of reviewed preparation requirements; MARKET reports demand-weighted coverage from sufficiently large posting samples. Do not present mixed evidence as market fit.
- EDITORIAL planning requires published criteria, template versions, and supporting evidence. Real corpus-backed results require a valid release_id; nullable release_id is reserved for development fixtures.
- Insufficient MARKET samples produce INSUFFICIENT_DATA. EDITORIAL is a separately identified analysis mode, not a silent fallback.
- Replace synchronous analysis creation with a 202 job response. Finalize FE and DB contract changes in M1.
- Confirm production PostgreSQL version (17 in the initial plan, 18 in the later report) and the pipeline scheduler with deployment owners.

## 4. Logical schema

Primary keys are UUIDs. Instants use PostgreSQL timestamptz (UTC storage and Asia/Seoul presentation); calendar dates use date. PostgreSQL does not preserve the original input offset, so store timezone and original_time_phrase separately. Check ownership against the authenticated user_id.

### Application tables

| Table | Principal columns and constraints |
|---|---|
| users | id PK, status, created_at |
| auth_identities / sessions | user_id FK, provider+subject UNIQUE / token_hash, expires_at |
| profiles | user_id PK/FK, version, major_raw, major_concept_id nullable, year, enrollment_status, expected_graduation_on |
| user_capabilities | id PK, user_id FK, category, raw_text, entity_id nullable, proficiency, verification, lifecycle, details JSONB, source_completion_event_id nullable |
| goals | id PK, user_id FK, occupation_id, target_by, timezone, original_time_phrase, status; partial UNIQUE for one active goal per user |
| route_preferences | user_id PK/FK, available_hours_per_week CHECK 1..60, availability_source, budget_mode, max_out_of_pocket_krw nullable CHECK >=0, flags |
| analyses | id PK, user_id FK, goal_id FK, profile_version, basis_type/version, release_id, methodology_version, status, input_snapshot JSONB, input_hash, results JSONB, generated_at |
| route_proposals | id PK, analysis_id FK, user_id FK, proposal_hash, profile_version, constraints_snapshot JSONB, feasibility, optimization_status, steps JSONB, decision_trace_id FK |
| roadmaps | id PK, user_id FK, goal_id FK, proposal_id FK, state, version, profile_version, release_id, validity JSONB; partial UNIQUE for one ACTIVE roadmap per user |
| roadmap_steps | id PK, roadmap_id FK, step_key, position, action_id, template_revision, state, planned_start/end, outcomes JSONB, criteria JSONB; UNIQUE(roadmap_id, step_key) |
| step_dependencies | roadmap_id, step_id, prerequisite_step_id; enforce same-roadmap references and prohibit self-edges |
| user_state_events | id PK, user_id FK, aggregate_id, version, kind, payload JSONB, created_at; append-only |
| step_completion_inheritances | step_id FK, original_completion_event_id FK; preserve the original completion basis |
| calculation_traces / decision_traces | id PK, user_id FK, input_hash, versions, release_id, outputs/support JSONB; solver traces include candidates, rejection reasons, seed, and reference time |
| recompute_requests | id PK, user_id FK, profile_version, trigger_event_id, state, resulting_analysis_id, proposal_id, error_code; UNIQUE(user_id, profile_version, trigger_event_id) |
| outbox_jobs | id PK, kind, payload, dedupe_key UNIQUE, state, attempt_count, available_at, lease_until, lease_token, last_error |
| idempotency_records | user_id, method, path, key, request_hash, response, expires_at; scoped UNIQUE |

Profile, goal, capability, and preference mutations compare and increment profiles.version in one transaction. A step completion affecting capabilities checks and increments both roadmap.version and profile.version. User events and recomputation/outbox records commit in that transaction. Only a job matching the current profile version may update the latest-analysis pointer.

### Published corpus contracts (DB-owned)

- Occupation: canonical ID, name, supported flag.
- Requirement: key, occupation_id, target/condition, minimum proficiency, necessity, satisfaction rule, support refs, basis version.
- ActionTemplate: stable ID + revision, prerequisites, typed outcomes, completion criteria, estimated hours, weekly workload, known/unknown cost, source/reviewer, supporting evidence.
- DatedOffering: template/course/credential reference, enrollment window, execution dates, eligibility, freshness.
- CorpusRelease: ID, state, data_as_of, methodology versions, source manifest.
- Evidence/Claim/Aggregate: stable ID, release membership, source URL, accepted evidence, numerator/denominator and trace where applicable.

Template revisions are immutable. Before publication, validate the prerequisite DAG, referenced outcome keys, positive effort estimates, and consistent cost states. Do not invent study-hour estimates or posting ratios to fill missing data.

## 5. FSD: functional specifications

| ID | Feature | Normal flow | Exceptions and acceptance criteria |
|---|---|---|---|
| F01 | Onboarding/profile | Enter capabilities/experience → confirm normalization → save | Preserve unresolved text without coverage credit; dates, time, and budgets never enter the normalizer |
| F02 | Goals/constraints | Save occupation, deadline, capacity, and budget | Reject past/too-soon deadlines, out-of-range capacity, and negative budgets |
| F03 | Gap analysis | Select basis version → evaluate satisfaction → persist results/evidence | Distinguish unknown from unmet; handle insufficient samples and revoked releases; planned outcomes do not count as current capabilities |
| F04 | Candidate generation | Find activities for gaps → recursively expand prerequisites | Report cycles/missing prerequisites; avoid repeating already-satisfied foundational work |
| F05 | Route calculation | Select candidates → validate time/budget/calendar → preview | Constraint conflicts produce INFEASIBLE; solver UNKNOWN produces timeout; never arbitrarily compress effort |
| F06 | Create/activate roadmap | User chooses proposal → DRAFT → explicit ACTIVATE | Recheck ownership, hash, profile version, release, and current validity; atomically replace previous ACTIVE roadmap |
| F07 | Step state | TODO↔IN_PROGRESS↔COMPLETED | Only ACTIVE roadmaps are mutable; completion creates SELF_REPORTED outcomes; reversal revokes only outcomes originating from that event |
| F08 | Recompute | Mutation → enqueue → refresh analysis/proposal | Never automatically rewrite saved roadmaps; stale jobs cannot replace current analyses |
| F09 | Evidence navigation | Requirement/step → source/trace | Enforce user ownership, release membership, and revocation checks |

RoutePlanner enforces prerequisites, weekly capacity, hard budget caps, real calendars, and mandatory outcomes by the deadline. Retain the DB plan's coverage/time/cost objective weights: REGULAR 55/30/15 and LOW_COST 35/20/45. fastest_path transfers 10 points from coverage and 5 from cost to time. Exclude unknown-cost activities when a hard monetary cap exists.

Start CP-SAT with sorted candidates, one worker, seed 20260904, and a 20-second limit. Record versions, inputs, reference time, and original results because wall-clock timeouts can affect reproducibility. Run a partial-route diagnostic only after mandatory constraints are proven infeasible. An INFEASIBLE proposal cannot become a saved roadmap. Completion within 80% of the horizon with known costs is FEASIBLE; completion by the deadline with less buffer or unknown costs is RISKY.

## 6. FE–BE integration contracts

### Planned product APIs (not implemented yet)

| Method / URL | Input or response |
|---|---|
| GET /api/v1/occupations | Supported occupations and analysis availability |
| GET/PUT /api/v1/me/profile | Profile / expected_profile_version |
| POST/PATCH/DELETE /api/v1/me/capabilities[/{id}] | Dedicated capability mutations, expected_profile_version |
| POST/PATCH /api/v1/me/goals[/{id}] | Goals/constraints, expected_profile_version |
| POST /api/v1/analyses | goal_id, basis_type, expected_profile_version → 202 {recompute_request_id, status_url} |
| GET /api/v1/recomputations/{id} | PENDING/RUNNING/READY/FAILED, result IDs, error |
| GET /api/v1/analyses/{id} | Analysis, evidence, input version, release |
| GET /api/v1/route-proposals/{id} | Immutable proposal, hash, constraint snapshot |
| POST /api/v1/roadmaps | analysis_id, proposal_id, expected_profile_version → DRAFT |
| GET /api/v1/roadmaps[/{id}] | Cursor-paginated list / detail |
| PATCH /api/v1/roadmaps/{id} | ACTIVATE/ARCHIVE, expected versions |
| PATCH /api/v1/roadmaps/{id}/steps/{step_id} | Target state, expected versions |
| GET /api/v1/claims/{id}/evidence?release_id=... | Published evidence |
| GET /api/v1/traces/{id} | User-owned calculation/decision trace |

Mutating POST requests accept Idempotency-Key; reusing a key with a different request hash returns 409. Standardize errors as `{error: {code, message, details, request_id}}`: 422 VALIDATION_ERROR/INSUFFICIENT_DATA, 409 VERSION_CONFLICT, 410 CORPUS_RELEASE_REVOKED, 503 SOLVER_TIMEOUT/DATA_UNAVAILABLE, 401 UNAUTHENTICATED, and 403 FORBIDDEN. Replace the scaffold's default FastAPI 422 response in M2.

FE polls every two seconds after job creation and stops at a terminal state. Job IDs allow polling to resume after disconnection. On 409, reload current state; on FAILED, display the reason and appropriate retry action. Creating a roadmap and activating it are separate user actions.

Review OpenAPI operation/schema changes in PRs. Generate FE TypeScript types with a tool such as openapi-typescript instead of maintaining duplicate handwritten contracts. Initially connect analysis/proposal components to `/dev` fixtures, then switch the adapter to product endpoints. Fixtures declare `is_fixture=true` and are disabled in production.

Recommended FE structure (documented here because no FE checkout is available):

```text
src/app/                         # providers, router
src/pages/{onboarding,analysis,roadmap}/
src/widgets/{gap-summary,roadmap-timeline}/
src/features/{edit-profile,request-analysis,activate-roadmap,update-step}/
src/entities/{profile,analysis,roadmap}/
src/shared/api/{generated,client}/
```

Separate query-cached server state from draft form state. Display server-calculated progress/coverage without recalculating it in FE. Development uses explicit localhost:5173→8000 CORS; production proxies /api from the FE gateway to the internal BE service. Finalize session cookies, CSRF, and the authentication provider in M1.

## 7. Implementation timeline

Estimate: six weeks with one BE developer and FE/DB coordination. Actual corpus publication may delay M4.

| Milestone | Timing | Deliverables | Verification/integration gate |
|---|---|---|---|
| M0 | Current | FastAPI, preview DTOs, five service boundaries, fixtures, docs, Railpack configuration | Server/OpenAPI/fixture smoke checks; deployment configuration review |
| M1 | Week 1 | Input/requirement/template schemas, basis-mode agreement, posting sample assessment, authentication choice | Four user scenarios, FE/DB contract agreement, review 10–15 draft templates |
| M2 | Week 2 | SQLAlchemy/Alembic, authentication, profile/goal/capability CRUD, error envelope | Persistence, ownership, optimistic locking, migration upgrade |
| M3 | Week 3 | Normalizer, GapAnalyzer, evidence DTOs, leased outbox | Existing/unknown capabilities, duplicate events, worker crash/reclaim, stale results |
| M4 | Week 4 | CandidateBuilder, CP-SAT RoutePlanner, traces, real corpus adapter | Dependencies, weekly capacity, budgets, infeasible/timeout cases, published-release smoke |
| M5 | Week 5 | RoadmapService, completion/reversal/inheritance, validity monitor | One ACTIVE roadmap, concurrent edits, reversal and revocation regression tests |
| M6 | Week 6 | FE E2E, Railpack-built deployment, restart recovery, finalized docs | Four user journeys: input→analysis→create→activate→complete |

Before corpus publication, use reviewed fixtures for algorithm development without serving them as production results. AI chat later reuses these five services through typed tools.

## 8. Layout and current implementation status

`src/jobtology_be/api` owns HTTP, `modules` owns features, `planning` owns candidate/solver boundaries, `corpus` owns published-data access, `infrastructure` owns database adapters, and `workers` owns background processing. `migrations` is application-only; `deploy` contains deployment documentation. Root `railpack.json` configures image creation and startup.

Implemented: liveness, Swagger/OpenAPI, opt-in analysis/route fixtures, settings validation, and Railpack configuration. Business services remain Protocols. Database access, authentication, actual analysis, solver, workers, and product endpoints follow the milestones above.

Production gates include authenticated ownership, explicit CORS/CSRF, release-revocation checks, migration readiness, worker lease/retry metrics, redacted structured logs, and API/worker resource limits. Coordinate PostgreSQL and Neo4j updates through the outbox rather than assuming distributed transactions.

## 9. CI/CD with Railpack

Railpack is the image builder. The deployment platform runs the resulting image and manages routing, runtime variables, health checks, and rollbacks. GitHub Actions runs the repository's lint and test checks; deployment should be gated on successful checks for the same commit.

- CI: install Python 3.12 and uv, run `uv sync --locked --dev`, `uv run ruff check .`, and `uv run pytest`.
- Build: use the repository root, `railpack.json`, `.python-version`, `pyproject.toml`, and committed `uv.lock`. The Python provider detects uv and installs dependencies. Pin the Railpack builder version in the deployment platform once its supported version is selected.
- Start: `uvicorn jobtology_be.main:app --host 0.0.0.0 --port ${PORT:-8000}`. The explicit module path supports the src layout.
- Runtime: set `JOBTOLOGY_ENVIRONMENT=production`, `JOBTOLOGY_ENABLE_FIXTURES=false`, and explicit allowed origins through the platform. Set PORT to the platform's assigned internal port.
- Health: use `/api/v1/health/live` for the scaffold. Add dependency-aware readiness before database-backed production traffic.
- Promotion: build the tested commit, run staging health/smoke checks, then promote that image. Retain the prior image for rollback.
- Migrations: once implemented, run Alembic once as a release task before traffic promotion, not independently in every API replica. Use backward-compatible migrations so image rollback remains possible.
- Workers: add a separate service from the same image once its executable entrypoint exists. It shares application configuration but has its own command, concurrency limit, and restart policy.

The repository provides configuration and CI checks; connecting the hosting platform and enabling deployment triggers remain platform setup tasks. See [deployment instructions](../deploy/README.md).
