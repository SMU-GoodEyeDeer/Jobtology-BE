# Product-role snapshot policy and opt-in runtime

The owner-approved rule policy is `config/product_roles/policy.v1.json` (also
packaged into the wheel). Its four role mappings, display names, additional
frontend base units, and role-local alias words are reviewable data. An alias
is only included when its target base code is present in the selected role's
official NCS input. `DATA_ANALYST` does not receive a SQL alias unless the
official role units contain its target base code.

The reader calls `catalog.product_role_inputs_v1(text[])` once at app lifespan
startup on the restricted catalog pool, in a READ COMMITTED, READ ONLY
transaction. From the latest READY NCS run it selects the highest version per
base code, excludes `(구버전)` names, and produces a **draft artifact**. Its full
SHA-256 digest covers the canonical snapshot content, display names, aliases,
requirement metadata, rule provenance, source identities, and the complete
curated official units/qualification/evidence inputs. A draft is not
a published corpus snapshot. NCS level at most 5 or linked posting evidence makes a
requirement REQUIRED; other units are PREFERRED. Evidence is source context,
not an official vacancy requirement or an eligibility determination. The
minimum positive qualification training hours is used when available; otherwise
the estimated duration is 20 hours. Demand uses `linked_postings` (DB migration
028): the denominator is the number of distinct linked postings (`<posting_source>:<posting_id>`,
currently `job_alio` and `nara_job`) linking any version of any unit selected for the role,
and each unit's numerator is the distinct postings among them linking any version of that
unit. `demand_pct = round(100 × numerator / denominator)` only when the denominator is at
least 5 (`DEMAND_MIN_BASE_POSTINGS`); otherwise it is null while `demand_postings` and
`demand_base` are still exposed. Inputs without `linked_postings` (DB before 028) keep all
three values null. The posting list and the demand rule are part of the approved artifact
digest, so applying 028 or new link publications requires a fresh artifact approval.
Evidence comes from reviewed, possibly historical link publications; it is not a vacancy
census and must not be presented as today's demand.

Skill-detail display fields `ncs_base_code` and `qualifications` are derived only
from the already-hashed `input_units` and `input_qualifications`. Qualifications
match the selected unit's exact versioned `code` and retain nullable names. Work-learning
names `<family>_L<level>_<version>` get `display_name` "<family> <level>수준" and
`kind=WORK_LEARNING`; only the newest standard per family/level is kept (`NNVn` beats
legacy `verX.Y`). Other names are `NATIONAL_TECHNICAL`; unnamed ones show their code.
National qualifications sort first. They travel through the existing
metadata lookup but are **not additional digest fields**: hashed `requirement_metadata`
retains exactly `ncs_level`, `demand_pct`, `estimated_hours`, `hours_basis`,
`demand_postings`, and `demand_base`. The canonical hashed document is unchanged for
identical inputs, so exposing these display fields does not require a new approval.

All runtime flags default to **off**:

The repository deployment config now selects the replacement artifact
`a550c8f9…192908e`. Its [2026-10-24 technical review](product-role-review-20261024.md)
reconstructs the exact previous approved artifact and verifies unchanged catalog
content/planning values under refreshed source pins. Activation is proposed for
owner review and merge; the running fallback image still uses the old approval.

| Environment variable | Effect when enabled |
|---|---|
| `JOBTOLOGY_PRODUCT_ROLES_ENABLED` | Load role inputs at startup and enable occupations/analysis from the generated release. Requires `JOBTOLOGY_CATALOG_DATABASE_URL` and the product database. A failed load keeps occupations and analysis at 503 without preventing process startup. |
| `JOBTOLOGY_PRODUCT_ROLE_ARTIFACT_APPROVAL_PATH` | Path to a separate owner-reviewed JSON record `{ "sha256": "<full draft digest>", "approved_by": "<reviewer>", "reviewed_at": "<timezone-aware ISO instant>" }`. Records live in `config/product_roles/approvals/<digest>.json`; the current deployment configuration selects `a550c8f9…192908e.json` ([2026-10-24 review](product-role-review-20261024.md)). The running fallback image uses the previous `524f1e46…0f7bd4.json` record, approved 2026-10-17. Missing/invalid/mismatched approval leaves the generated draft unpublished and the runtime at 503. |
| `JOBTOLOGY_INPROCESS_WORKER_ENABLED` | While a role release is loaded, process at most one queued local-json-labeled recompute every three seconds. A READY feasible targeted analysis creates and activates a titled roadmap only when the goal has no ACTIVE roadmap; later recomputes (e.g. after step completion) keep the existing roadmap. |
| `JOBTOLOGY_CAPABILITY_LIST_AUTHORITATIVE` | Treat the stored capability list as complete; unmatched requirements are UNMET, including an empty capability list. |

Only after an exact full-digest match is the already-reviewed draft converted
to a validated `PUBLISHED` reader; its `reviewed_at` comes solely from that
separate approval record. A new ingestion run, policy name/alias, template,
or metadata change generates a new digest and fails closed until separately
reviewed. Persisted release selections are never redirected to a newer draft:
an unavailable prior release fails closed. The published release ID embeds the
entire SHA-256 digest; approval compares the full digest independently.

The generated snapshot's internal selection source label remains `local_json`
even when `JOBTOLOGY_CORPUS_SOURCE=neo4j_query_api`, because the worker's
existing published-snapshot dispatch supports that label. This label names
the **adapter path**, not the source of the official NCS data. The immutable
inputs and support references carry the NCS run and publication identities.
The policy approval record approves mapping/rules, not a generated artifact.
Its exact approval time was not verified and is intentionally absent; it never
supplies the published snapshot review timestamp. Review the draft's exact
content and record its full digest before turning on these flags.

Analysis results freeze the requirement metadata from their selected published
snapshot. Historical results therefore keep their original levels, duration,
qualifications and demand context. A legacy result without frozen metadata may
use the current holder only when its full release ID exactly matches; otherwise
metadata remains unavailable. Publishing the derived display metadata does not
rewrite the approved artifact or its digest.

JobKorea metadata-to-occupation associations are exposed separately as inferred
posting context, with review status and source/catalog freshness. They do not
silently extend `product_role_inputs_v1` or the reviewed competency denominator.
The Neo4j graph excludes stale associations from current counts and fetches
O*NET details by the exact selected current revision ID.

OR-Tools remains the bounded activity scheduler. Results persist exact hourly
allocations and solver diagnostics (full/partial status, objective/bound,
elapsed time, worker count, seed and budget); a conservative capacity check can
skip a provably infeasible full solve while retaining partial planning. Timeout
diagnostics are retained on the failed request. Equivalent active analysis
requests reuse the same context; an explicit refresh can rerun a completed one.
Activity cost remains zero until pricing is modeled. Missing reviewed prerequisite
edges or NCS–O*NET equivalences are not inferred as verified constraints.
