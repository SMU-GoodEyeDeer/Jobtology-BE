# Product-role catalog review — 2026-11-02

This PR proposes activation of artifact
`e0694fb99d45a2883e4e7dc75cbb4a7c01fe8ef8474054574b7c03aa22b191f5`.
The record identifies the technical reviewer; owner review and merge authorize
activation through the existing CI/Coolify deployment flow. No live approval
file, running application, environment variable, catalog publication or user
record was changed while preparing this review.

## Deployment failure

The new image for main commit `3cc81e8` starts successfully, but catalog startup
produces the new digest above while Railpack selects approved artifact
`a550c8f91685408cb5903eb67ffcf8e1b8eab6be3d3fc39501d7c654f192908e`.
The holder rejects that mismatch, so `/api/v1/health/ready` returns 503 and
Coolify rejects the new container. The previous October 27 container remains
healthy. Healthcheck requests reach the application; changing probe paths,
timeouts or the management-token setting does not repair this catalog mismatch.

## Exact baseline comparison

The current artifact was read from `catalog.product_role_inputs_v1` in a bounded
read-only transaction through the running backend's restricted catalog
connection. The current branch's builder independently reproduces its exact
SHA-256. Replacing only the two NCS source run IDs and their exact timestamps,
including those IDs embedded in support references, reconstructs the entire
previously approved canonical document and **exactly** reproduces `a550c8f9…192908e`.
All 172 changed leaf paths are accounted for by those four metadata substitutions.
An independent repeatable-read, read-only catalog comparison also rebuilt the
old artifact from its retained READY physical/archive rows and reproduced the
exact previous approval. The old/new curated input rows are equal as multisets;
current SQL inputs and sources match the captured current artifact.

| Source | Previously approved pin | Current pin |
| --- | --- | --- |
| NCS competencies | `ce5655eb-8881-47bf-affc-81158719b43d`, 2026-10-23T10:47:43.753991+00:00 | `5f5959bd-b965-436a-830a-db75efafd7b8`, 2026-10-30T12:17:41.868092+00:00 |
| NCS qualifications | `c56de59b-3514-45e8-9b8a-a7a60945276d`, 2026-10-23T13:27:13.061398+00:00 | `3fabbefb-5499-4d66-a4c8-92845a3c74fe`, 2026-10-30T15:03:46.057215+00:00 |

The full-document hash comparison establishes unchanged content for all 89
competency rows, 82 qualification mappings, 15 evidence entries and 15 linked
posting entries. Role mappings, aliases, names, necessity, levels, durations,
costs, demand values, provenance and templates are identical under the pin
substitution. The roles retain 19 AI, 40 backend, 14 frontend and 11 data-analysis
requirements and corresponding templates (84 total). This is technical content
equivalence, not a new individual review of historical posting associations or
an employer-stated competency claim; their existing review/freshness status is
preserved.

Protected mode-0600 evidence is held under
`~/.local/state/jobtology-be/product-role-review-20261102/`:
`current-artifact.json`, `reconstructed-a550-document.json`,
`pin-only-reconstruction-receipt.json`, `candidate-readiness.json`,
`archive-input-comparison.json` and `archive-reconstruction-receipt.json`.
The archive comparison SHA-256 is
`0a345676e32dd58c0f69a12fbf3389ca4e3099d0c67efb98aa812344c48d43e8`.
These are operator review artifacts, not deployed runtime data or credentials.

## Validation and rollout

A temporary approval record was tested in a separate holder instance against
the real read-only catalog. All four role snapshots loaded, and the application
DB/catalog readiness checks passed. The temporary files were removed; the
running holder and recompute worker were not changed or exercised by that
probe. Existing holder/approval tests retain missing/mismatched approval
rejection and readiness failure behavior.

`railpack.json` selects the new checked-in approval record. The inspected
Coolify configuration has no explicit Runtime override for
`JOBTOLOGY_PRODUCT_ROLE_ARTIFACT_APPROVAL_PATH`, so the new image uses that
selection. If an override is added later, it must select the matching record.
Merge the reviewed PR and redeploy the resulting main image, retaining
`/api/v1/health/ready`. No SQL migration, token rotation or healthcheck change is
required for this fix. Verify the new container becomes healthy and the catalog
startup mismatch disappears before removing the fallback image.

Future latest-READY pin/content changes still invalidate the full artifact
digest. This fix preserves that guard. Avoiding repeated approval work for
unrelated deployments requires a separate design to load an immutable approved
snapshot; this PR does not silently drop source identity from the digest or
rewrite stored analyses/roadmaps.
