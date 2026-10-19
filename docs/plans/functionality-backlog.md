# Functionality fixes after Google authentication

Items 1–7 from the October 19 audit are implemented for review in this release. Items 8–12
remain queued. Authentication, guest account removal, and account quotas were shipped
separately. Backups remain deferred at the owner's request. Implementation status does not
mean the new release or its migrations have been deployed.

| Order | Issue | Evidence | Acceptance criteria |
| --- | --- | --- | --- |
| 1 | Preserve solver dates when saving roadmaps | `infrastructure/persistence/store.py` omits planned dates from step inserts; all 324 production steps had null dates during the audit. | Saved steps retain proposal dates; dashboard due dates work; comparison with the original proposal reports no artificial date changes. |
| 2 | Make the solver schedule authoritative for weekly plans | `application/weekly_plan.py` independently packs tasks, derives prerequisites from NCS levels, and does not enforce the target date. | Persisted dependencies survive; changes to capacity, rest weeks, order, and estimates are checked against planning constraints; late completion is allowed with an explicit deadline warning; previews and reset use the same server implementation. |
| 3 | Correct cost data and hard-budget outcomes | Every generated product-role activity has UNKNOWN cost; a hard cap excludes all such activities. | Use an explicit versioned 0-won modeling assumption for current product activities, as requested; hard 0-won and 1,000,000-won caps retain achievable routes. Preserve conservative unknown-cost handling outside this product policy. |
| 4 | Preserve recompute context during concurrent edits | `configured_recompute.py` excludes RUNNING contexts when carrying inputs forward. | An edit during the first solve creates a current-version result; stale jobs cannot replace current results; missing-context failures are visible and actionable. |
| 5 | Retry automatic roadmap creation durably | Recompute completes before roadmap creation and activation; post-processing failures are not retried. | A durable job or reconciler retries after process/SQL failures; repeated delivery creates at most one active roadmap; creation and activation recover together. |
| 6 | Validate goals before persistence | Invalid timezone/deadline can be accepted then fail during analysis. | Invalid role/timezone/past deadline/unsupported horizon return actionable 422 responses before persistence. Unavailable role catalogs return 503. |
| 7 | Implement explicit planning preferences | `needs_portfolio` and `career_switch` currently change neither candidates nor objective. | Portfolio preference produces project deliverables; foundational-learning preference schedules unmet foundations earlier after primary feasibility objectives. Both preserve approved source provenance and real prerequisites. |
| 8 | Recover catalog loading and expose readiness | Product-role loading runs once; a failed startup load can leave the worker unavailable while liveness stays healthy. | Bounded retries recover transient failures; readiness checks application DB, approved role availability, and worker health; approval mismatches remain fail closed. |
| 9 | Filter lifecycle and bound list queries | Active-capability reads include revoked rows; roadmap reads have unbounded lists and repeated detail queries. | Active reads exclude revoked records; pagination and batched roadmap queries have stable ordering. |
| 10 | Build graph-derived planning candidates | Current activities cover one unit each and contain no prerequisites, alternatives, or meaningful cost data. | Curate one role's reviewed prerequisite/activity subgraph, project bounded versioned inputs to CP-SAT, preserve relationship provenance, and compare results against a simple scheduling baseline. Taxonomy and co-occurrence are not assumed to be learning prerequisites. |
| 11 | Keep backend and database contracts compatible | Two integration fixtures fail against the newer sibling DB checkout but pass against the DB commit pinned by BE CI. | Publish the supported DB/BE revision pair and run contract checks before changing either deployed component. |
| 12 | Validate capability metadata before persistence | Goal validation is covered in this release; capability metadata has separate category-specific constraints. | Reject unsupported experience metadata with field-specific 422 responses before analysis. |

This release also adds verified Google display names and permanent self-service Jobtology
account deletion. Deletion is transactional, removes all account-owned records and sessions,
preserves other users/shared credentials, and cannot be reversed from within the app.

The roadmap migration repairs only missing dates matched uniquely to the original proposal;
it preserves existing dates, completion progress, and custom weekly settings. Permanent
source conflicts settle automatic roadmap follow-ups; transient database failures retry.

OR-Tools is already in use. Improve input data and preserve its output before considering
a solver replacement. Native Neo4j evidence reads and the approved product-role snapshot
are distinct contracts; the current evidence graph is not itself a learning dependency graph.
