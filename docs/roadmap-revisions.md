# Reviewed roadmap revisions

A completed analysis can offer a new proposal while the current roadmap remains active. `GET /api/v1/dashboard` exposes its `proposal_id`, tied to the current profile's ready analysis. A missing proposal is not an instruction to replace a roadmap.

`GET /api/v1/roadmaps/{roadmap_id}/revision?proposal_id=...` compares the current owned roadmap and proposed replacement. It returns the proposal hash, profile version, roadmap version, weekly plan version, feasibility, schedule/constraint changes, completed history to preserve, and in-progress steps that can or cannot carry forward. Comparisons do not write data.

`POST /api/v1/roadmaps/{roadmap_id}/revision` accepts:

```json
{
  "proposal_id": "<reviewed proposal UUID>",
  "expected_proposal_hash": "<reviewed proposal hash>",
  "expected_profile_version": 4,
  "expected_roadmap_version": 3,
  "expected_plan_version": 1
}
```

An optional `title` names the replacement. Adoption checks ownership, the active targeted goal, latest analysis/proposal, feasibility, deadline, all versions and the immutable proposal hash. The transaction creates the replacement, retains verified completed history and matching in-progress work, and activates it while archiving the predecessor. A failure rolls back every write; a stale comparison returns 409 and requires another review. Concurrent adoption can activate only one replacement.

Completed history retains the original completion event, outcomes, criteria, pinned effort, source metadata and saved custom dates/allocations. Adoption creates no completion event or competency credit. Missing or mismatched completion lineage, or unknown historical effort, makes the comparison non-actionable (`COMPLETED_HISTORY_UNVERIFIED` / `COMPLETED_HISTORY_UNAVAILABLE`). The existing roadmap remains active. An in-progress activity absent from the new proposal stays in the archived predecessor and is disclosed before adoption.

`resets_future_customization` identifies a customized weekly plan: future order, hours and rest weeks return to the reviewed proposal schedule. Completed custom history remains fixed, and the archived plan retains its customization. The comparison uses the saved current weekly capacity and saved exact schedule when available. Writes to an archived weekly plan return 409.

`GET /api/v1/roadmaps/{roadmap_id}/skills` returns `{roadmap_id, analysis_id, release_id, skills}` from the roadmap's own origin analysis, including `SATISFIED` skills. Carried completed activities additionally retain their original requirement snapshots. Each skill includes `source_analysis_id` and `step_ids`; detail panels select by exact step ID before falling back to requirement key. This matters when the same requirement key exists in different releases. Unknown legacy metadata remains unknown; only an exact release match can supplement it.

Weekly custom schedules honor saved local availability windows and unavailable dates. Completed intervals and solver intervals remain immutable. The custom calculator searches at most 520 future weeks and returns 422 if the requested work cannot fit that bounded calendar.
