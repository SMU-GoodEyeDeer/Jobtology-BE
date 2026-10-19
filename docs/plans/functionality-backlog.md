# Functionality fixes after Google authentication

These issues are queued from the October 19 audit. Authentication, guest account removal,
and account quotas are handled separately. Backups are deferred at the owner's request.

| Order | Issue | Evidence | Acceptance criteria |
| --- | --- | --- | --- |
| 1 | Preserve solver dates when saving roadmaps | `infrastructure/persistence/store.py` omits planned dates from step inserts; all 324 production steps had null dates during the audit. | Saved steps retain proposal dates; dashboard due dates work; comparison with the original proposal reports no artificial date changes. |
| 2 | Make the solver schedule authoritative for weekly plans | `application/weekly_plan.py` independently packs tasks, derives prerequisites from NCS levels, and does not enforce the target date. | Persisted dependencies survive; changes to capacity, rest weeks, order, and estimates are checked against planning constraints; late completion has an explicit violation or infeasibility result. |
| 3 | Correct cost data and hard-budget outcomes | Every generated product-role activity has UNKNOWN cost; a hard cap excludes all such activities. | Distinguish verified free, known paid, and unknown-cost activities; show missing price data explicitly; test realistic feasible and infeasible budgets without treating unknown as free. |
| 4 | Preserve recompute context during concurrent edits | `configured_recompute.py` excludes RUNNING contexts when carrying inputs forward. | An edit during the first solve creates a current-version result; stale jobs cannot replace current results; missing-context failures are visible and actionable. |
| 5 | Retry automatic roadmap creation durably | Recompute completes before roadmap creation and activation; post-processing failures are not retried. | A durable job or reconciler retries after process/SQL failures; repeated delivery creates at most one active roadmap; creation and activation recover together. |
| 6 | Validate goal and capability inputs before persistence | Invalid timezone/deadline and malformed capability metadata can be accepted then fail during analysis. | Invalid role/timezone/past deadline/unsupported horizon/experience metadata return actionable 422 responses before persistence. |
| 7 | Implement or remove ineffective preferences | `needs_portfolio` and `career_switch` currently change neither candidates nor objective. | Each supported preference changes a relevant activity, outcome, or constraint, with a meaningful regression scenario. |
| 8 | Recover catalog loading and expose readiness | Product-role loading runs once; a failed startup load can leave the worker unavailable while liveness stays healthy. | Bounded retries recover transient failures; readiness checks application DB, approved role availability, and worker health; approval mismatches remain fail closed. |
| 9 | Filter lifecycle and bound list queries | Active-capability reads include revoked rows; roadmap reads have unbounded lists and repeated detail queries. | Active reads exclude revoked records; pagination and batched roadmap queries have stable ordering. |
| 10 | Build graph-derived planning candidates | Current activities cover one unit each and contain no prerequisites, alternatives, or meaningful cost data. | Curate one role's reviewed prerequisite/activity subgraph, project bounded versioned inputs to CP-SAT, preserve relationship provenance, and compare results against a simple scheduling baseline. Taxonomy and co-occurrence are not assumed to be learning prerequisites. |
| 11 | Keep backend and database contracts compatible | Two integration fixtures fail against the newer sibling DB checkout but pass against the DB commit pinned by BE CI. | Publish the supported DB/BE revision pair and run contract checks before changing either deployed component. |

OR-Tools is already in use. Improve input data and preserve its output before considering
a solver replacement. Native Neo4j evidence reads and the approved product-role snapshot
are distinct contracts; the current evidence graph is not itself a learning dependency graph.
