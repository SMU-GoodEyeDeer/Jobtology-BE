# Historical NCS posting associations — 2026-10-24

Read-only inspection on Goldship found 2,309 JobKorea posting-to-NCS associations
across the six occupation codes used by the four product roles. All 2,309 retain
matching source hashes, but none has a current catalog pin.

| NCS occupation | Associations | Current source | Current catalog |
| --- | ---: | ---: | ---: |
| 20010105 — 빅데이터분석 | 95 | 95 | 0 |
| 20010202 — 응용SW엔지니어링 | 1,855 | 1,855 | 0 |
| 20010204 — DB엔지니어링 | 35 | 35 | 0 |
| 20010207 — UI/UX엔지니어링 | 40 | 40 | 0 |
| 20010703 — 인공지능모델링 | 126 | 126 | 0 |
| 20010705 — 인공지능서비스구현 | 158 | 158 | 0 |

These are inferred metadata associations, not reviewed competency requirements.
`source_freshness=CURRENT` means the stored source hash still matches; it does not
mean the vacancy remains open. Posting status and deadlines must also be checked.

The latest READY NCS run is `ce5655eb-8881-47bf-affc-81158719b43d`.
All READY metadata-occupation graph publications reference
`ba32f673-255c-4990-80d4-8dbef6008e2f`. Prepared matching catalogs and retrieval
contexts exist only for that older run and
`64c20b47-0575-476e-9a28-d58d16d8c939`; neither contains the current run.
The live contract deliberately marks an NCS association stale when its publication's
NCS run differs from the latest READY run.

The product-role approval refresh separately compared 89 role competency inputs.
It does not establish equivalence of the complete 1,094-occupation matching catalog,
hierarchy, names, unit sets and retrieval context used to infer posting associations.
There is insufficient prepared current-run evidence to relabel these links current.
This change therefore exposes historical groups with their original pins and stale
labels; it does not rewrite or republish them.

To produce current associations, use the catalog ingestion/matching pipeline to:

1. Prepare the matching catalog and hierarchy/retrieval context for the current
   READY NCS run.
2. Compare canonical target facts, hierarchy and retrieval context with the pinned
   publication. Re-run inference/review where equivalence cannot be established.
3. Publish through the existing guarded metadata-occupation graph workflow, preserving
   review provenance and source hashes. Do not update pin/freshness columns directly.
4. Verify the live contract's per-occupation summary before treating links as current.

The following is a read-only verification query in the **catalog database**:

```sql
SELECT catalog.inferred_occupation_context_v1(
  ARRAY['20010105', '20010202', '20010204', '20010207', '20010703', '20010705'],
  1, 0
)->'summary';
```

No catalog writes, paid re-matching, source publication or private account changes
were performed for this report. O*NET associations have their own content-hash/release
checks and remain separately identified; they do not imply NCS equivalence.
