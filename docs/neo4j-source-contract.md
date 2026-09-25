# Neo4j Source Contract — Bounded Live Observation Record

Refreshed: 2026-09-25 after tailnet restoration

## Status and inspection boundary

**Live source observations are recorded below.** They come from a bounded, authenticated,
read-only Query API inspection, not from the local JSON fixture or an inferred application
model. An earlier local DNS failure was environmental and is superseded by this successful run.

The disposable probe combined the configured host-and-port `db_link` with its separately typed
Bolt/Neo4j `db_protocol` in memory, parsed that source URI once through `SecretStr`, and derived
the HTTPS Query API endpoint at port 443. It printed only `expected_configured_host=true`.
Neither the source URI, password, raw identifier, response body, nor text field was retained.

- `POST /db/neo4j/query/v2` authenticated as the configured/default Neo4j user and database.
- `accessMode: "Read"`, TLS verification enabled, redirects disabled, and a 10-second client
  timeout on every request.
- Fixed Cypher only; no write statement, caller-provided Cypher, or large export was used.
- Node samples were capped at 5–10 rows, current payloads at 3 rows, and relationships were
  aggregated as counts, keys, and `valueType` values rather than property values.

## Production repository smoke

After the native read client and repository were implemented, one authenticated production-path
smoke invoked `Neo4jQueryApiReadClient`, `NEO4J_CORPUS_READ_QUERY_CATALOG`, and
`Neo4jCorpusRepository` directly. It used `Neo4jPagination(limit=1, offset=0)` for each fixed
query and retained no source values or identifiers.

- `list_occupations`: 1 bounded typed row.
- `list_publications`: 1 bounded typed row.
- `list_alignments` for the internally selected publication: 1 bounded typed row.
- Repository page-bound validation passed; no transport, upstream-query, or parse error occurred.
- The fixed alignment projection carries only strict source enrichment context (`id`, `posting_id`,
  `current`) for its relation row. It does not expose raw payload content, payload hashes, or
  reviewer/candidate/provenance fields.
- No Neo4j mutation, PostgreSQL access, or raw response output occurred.

## Live label inventory

| Label | Count |
| --- | ---: |
| `reviewedNcsPublication` | 1 |
| `reviewedNcsEnrichment` | 155 |
| `jobEnrichment` | 155 |
| `jobPosting` | 11,812 |
| `ncsCompetency` | 28,384 |
| `occupation` | 1,111 |
| `entity` | 41,710 |
| `organization` | 355 |
| `qualification` | 48 |
| `ingestionBatch` | 37 |
| `ingestionRecord` | 73,427 |

No dedicated activity-template label appeared in this live label inventory.

## Observed node property contract

Property rows below are key/type samples only; no values or identifiers were recorded.

| Label | Observed key/type shape |
| --- | --- |
| `reviewedNcsPublication` (1 sampled) | `id:string`, `publication_id:string`, `postings:integer`, `state:string`; observed finite state: `READY`. |
| `reviewedNcsEnrichment` (10 sampled) | `current:boolean`, `id:string`, `managed_by:string`, `name:string`, `payload_hash:string`, `payload_json:string`, `posting_id:string`, `publication_id:string`. |
| `jobPosting` (5 sampled) | `code`, `id`, `kind`, `name`, `name_source_record_id`, `name_source_run_id`, and `title`, all strings. |
| `occupation` (5 sampled) | `code`, `id`, `kind`, `name`, `name_source_record_id`, and `name_source_run_id`, all strings. |
| `ncsCompetency` (5 sampled) | Same sampled catalog shape as `occupation`: `code`, `id`, `kind`, `name`, `name_source_record_id`, and `name_source_run_id`, all strings. |

## Publication, history, and absence observations

The graph has a real non-null `reviewedNcsEnrichment.current` boolean: the complete
distribution is 9 `true` and 146 `false` rows. Each group references one distinct
`publication_id`.

| `current` | Enrichments | Distinct referenced publication IDs | Matching `reviewedNcsPublication` nodes |
| --- | ---: | ---: | ---: |
| `true` | 9 | 1 | 1 |
| `false` | 146 | 1 | 0 |

This establishes an observed current-versus-unmatched-historical topology. It does **not**
establish that `false` means revoked, deleted, withdrawn, or invalid. The only materialized
publication node has source state `READY`; `READY` is not yet an application `PUBLISHED` mapping.

`payload_json` and `payload_hash` are both non-null on all 155 enrichment nodes. That proves
their presence in this release, not a general rule for all future source releases.

## Observed reviewed-source relationships

| Pattern | Count | Property schema |
| --- | ---: | --- |
| `(entity:jobPosting)-[:HAS_ENRICHMENT]->(jobEnrichment:reviewedNcsEnrichment)` | 155 | No relationship properties observed. |
| `(jobEnrichment:reviewedNcsEnrichment)-[:ALIGNS_WITH_NCS]->(entity:ncsCompetency)` | 388 | `accepted: BOOLEAN NOT NULL`, `decision_id: INTEGER NOT NULL`, `publication_id: STRING NOT NULL`; the remaining observed property names are listed below without values. |

`ALIGNS_WITH_NCS.accepted` is a real finite boolean with 3 `true` and 385 `false` relationships.
Its business meaning is not yet mapped to an application requirement, capability, or release rule.

The full observed safe property-name set for `ALIGNS_WITH_NCS` is `accepted`,
`candidate_actor`, `candidate_id`, `candidate_origin`, `decision_id`, `duty`,
`evidence_json`, `origin`, `publication_id`, `reason`, `reviewer`, and `reviewer_kind`.
Only the three non-sensitive property types above were retained by the original relationship
aggregation; the remaining names are schema identifiers, not documented values.

## Current `payload_json` shape and hash convention

Three current payload strings were parsed in memory and discarded. Every sampled stored hash
equalled `sha256(payload_json.encode("utf-8"))` exactly: **3 checked, 3 matched**. A follow-up
shape-only parse merged the three decoded JSON structures below. It retained field names, JSON
types, and aggregate element counts only—never source values, identifiers, quotes, or text.

All 16 top-level fields below were present in all three current payloads.

| Top-level field | JSON type |
| --- | --- |
| `extraction` | object |
| `extraction_decision_id` | integer |
| `extraction_model` | string |
| `extraction_reviewer` | string |
| `extraction_reviewer_kind` | string |
| `item_id` | string |
| `job_run_id` | string |
| `links` | array of objects |
| `name` | string |
| `ncs_run_id` | string |
| `posting_id` | string |
| `prompt_version` | string |
| `revision_id` | string |
| `source_hash` | string |
| `source_id` | string |
| `source_posting_id` | string |

All seven fields below were present in all three `extraction` objects.

| `extraction` field | JSON type | Current-sample observation |
| --- | --- | --- |
| `duties` | array of objects | 5 total objects across 3 payloads |
| `duties_status` | string | present 3/3 |
| `extraction_scope` | string | present 3/3 |
| `positions` | array of objects | 16 total objects across 3 payloads |
| `requirements` | array | present 3/3; 0 total items |
| `schema_version` | string | present 3/3 |
| `source_metadata` | object | present 3/3 |

`extraction.duties[*]` was an object with `evidence:object`, `evidence_ids:array[string]`,
`position:string`, `position_ids:array[string]`, `position_names:array[string]`,
`text:string`, and `text_parts:array[string]`. Its `evidence` object had `field:string` and
`quote:string`.

`extraction.positions[*]` was an object with `evidence:object`, `evidence_ids:array[string]`,
`id:string`, and `name:string`; `evidence` again had `field:string` and `quote:string`.
`extraction.source_metadata` had `organization_name:string`, `regions:string`, and
`title:string`. No nested `item` object appeared in these samples; the observed item reference
was `item_id:string`.

`links[*]` was an object with `candidate_actor:string`, `candidate_id:string`,
`candidate_origin:string`, `competency_code:string`, `decision_id:integer`, `duty:object`,
`duty_index:integer`, `ncs_run_id:string`, `reason:string`, `review_notes:string`,
`reviewer:string`, and `reviewer_kind:string`. Its `duty` object used the same shape as
`extraction.duties[*]`. This is a payload-local link shape; it must not be assumed to be
identical to an `ALIGNS_WITH_NCS` relationship property contract.

The three observed `requirements` arrays were empty. That is a sample-shape observation only,
not proof that requirements are absent from every source payload or relation.

## Existing application contract and still-missing planner data

The current app-side `PublishedCorpusSnapshot` consumer still requires an occupation/basis/release
identity, release review metadata, capability aliases, allowed experience codes, requirements
with evidence, and activity templates with revision, estimated hours, prerequisites, outcomes,
completion criteria, support references, cost, and foundational state.

The live graph now provides a publication ID, payload hash, current marker, posting/NCS alignment,
and catalog nodes. It does **not yet demonstrate** a source mapping for:

- application `occupation_id`, `basis_version`, or `release_id`;
- source `READY` to app `PUBLISHED`, reviewed time, fixture state, expiry, revocation, or validity;
- requirement keys/necessity/support references/experience codes;
- activity templates, duration/cost, prerequisite DAG, completion criteria, or foundational state;
- the meaning of `ALIGNS_WITH_NCS.accepted` as an application capability or editorial outcome.

Therefore a future adapter may pin and verify the observed source publication/hash data, but it
must keep the above gaps explicit and fail closed rather than synthesize a usable planner snapshot.
The app's persisted `source_validity` default is separate from this upstream graph and must not be
used to assign meaning to a missing upstream property.

## Implementation guardrails

1. Treat `publication_id` plus the verified raw UTF-8 payload hash as source facts, not as the
   app's release identity until contract-model defines a versioned mapping.
2. Model current and historical rows separately. A historical row's unmatched publication node is
   observed, but revocation remains unproven.
3. Preserve the source state `READY` verbatim at the adapter boundary until an explicit mapping
   policy is approved.
4. Keep candidate/reviewer/text content out of logs, API responses, documentation, and tests;
   only structural counts and typed fields belong in the source contract.
5. Build no planner/template behavior from empty arrays or from the absence of an activity-template
   label alone; both observations require a deliberate missing-data outcome.
