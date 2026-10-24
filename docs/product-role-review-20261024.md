# Product-role catalog review — 2026-10-24

This PR proposes activation of catalog artifact
`a550c8f91685408cb5903eb67ffcf8e1b8eab6be3d3fc39501d7c654f192908e`.
Owner review and merge approve deployment of the refreshed source pins. The
approval record identifies the technical reviewer; it does not claim a prior
owner approval of this new digest. No running service or catalog publication
was modified while preparing this review.

## Exact baseline and comparison

The previous approved artifact is
`524f1e46a84ef9df3c8b859333bb73caa9243593edd2393fd27bb8a0650f7bd4`.
It was reconstructed from archived NCS inputs, the checked-in policy and the
current unchanged reviewed link inputs. Its canonical SHA-256 exactly matches
that previous approval, establishing the comparison baseline.

| Input | Previous run | Refreshed run |
| --- | --- | --- |
| NCS competencies | `26b8f6cc-c32b-4e3a-a529-a37a329b898b` — 2026-10-16 | `ce5655eb-8881-47bf-affc-81158719b43d` — 2026-10-23 |
| NCS qualifications | `d0e3560e-7984-48c8-82fd-2333a36e6044` — 2026-10-16 | `c56de59b-3514-45e8-9b8a-a7a60945276d` — 2026-10-23 |

- All 89 official competency input rows are unchanged.
- All 82 qualification mapping rows are unchanged as a multiset, including
  names and training hours. Two SQL-utilization mappings share the same
  competency/qualification identifiers but have minimum hours 45 and 60;
  both values existed previously. Their row order changed.
- All 15 reviewed evidence entries and 15 linked-posting entries are unchanged.
  Publication identities, provenance and freshness flags are unchanged.
- Role mappings, aliases, necessity, levels, durations, costs, demand values
  and template content are unchanged. The four roles retain 19 AI, 40 backend,
  14 frontend and 11 data-analysis requirements and templates (84 total).
- After replacing the old NCS run IDs/timestamps with the new source pins
  (including support references) and canonically ordering qualification rows,
  the entire baseline artifact equals the new artifact.

The builder previously sorted qualifications by only competency and qualification
code. Equal keys retained database row order, making approval hashes unstable.
The new tie-break uses every serialized field, including nullable values;
permutation tests prove stable output without deleting or changing either row.
The resulting current digest is exactly the proposed digest above.

## Deployment and limits

`railpack.json` selects the replacement approval record and adds `wget` alongside
the existing `curl` for Coolify's fallback probe. No explicit Coolify override of
the approval-path setting was present when inspected. No SQL migration is needed.
The readiness endpoint remains `/api/v1/health/ready`; catalog approval checks
remain enforced. Do not switch to liveness to hide unavailable catalog services.

The start command also uses `mise exec -- uvicorn` so Python receives the installed
Java runtime's `JAVA_HOME`. In the built image, plain Python could execute Java
but JPype could not locate its JVM. An isolated image check with `mise exec`
discovers all five document-format runtimes, including HWP. This does not claim
successful parsing of every document template.

This review does not revalidate or republish historical posting-to-NCS links,
rewrite their freshness flags, or change stored analyses/roadmaps. Future source
pin/content changes still require a separately reviewed artifact. Preventing
weekly ingestion from blocking deployments would require an explicit design
change to load an immutable pinned snapshot instead of the latest READY inputs.

The Authlib JOSE deprecation warning is unrelated to readiness; migrating that
library is a separate change.
