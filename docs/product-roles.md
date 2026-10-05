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
the estimated duration is 20 hours. Demand percentages are null because these
source counts do not define a statistically meaningful denominator.

All runtime flags default to **off**:

| Environment variable | Effect when enabled |
|---|---|
| `JOBTOLOGY_PRODUCT_ROLES_ENABLED` | Load role inputs at startup and enable occupations/analysis from the generated release. Requires `JOBTOLOGY_CATALOG_DATABASE_URL` and the product database. A failed load keeps occupations and analysis at 503 without preventing process startup. |
| `JOBTOLOGY_PRODUCT_ROLE_ARTIFACT_APPROVAL_PATH` | Path to a separate owner-reviewed JSON record `{ "sha256": "<full draft digest>", "approved_by": "<reviewer>", "reviewed_at": "<timezone-aware ISO instant>" }`. Owner-approved records live in `config/product_roles/approvals/<digest>.json`; the deployed record is `ae6fec57…83fc6.json` (approved 2026-10-05). Missing/invalid/mismatched approval leaves the generated draft unpublished and the runtime at 503. |
| `JOBTOLOGY_INPROCESS_WORKER_ENABLED` | While a role release is loaded, process at most one queued local-json-labeled recompute every three seconds. A READY feasible targeted analysis with no ACTIVE roadmap for the goal/profile version creates and activates a titled roadmap. |
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
