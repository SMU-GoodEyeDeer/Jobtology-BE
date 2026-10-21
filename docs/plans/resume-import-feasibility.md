# Résumé import feasibility

**Implementation update:** The scanner is implemented end to end with stateless processing, sanitized HTML/text review and optional PDF-only paragraph AI extraction (three pages maximum). See [the current API, retention, bounds and limitations](../resume-scanner.md). The persistent job endpoints/24-hour storage discussed below remain design alternatives, not active APIs.

Assessed 2026-10-20 on `feat/functionality-improvements`. **Feasible as an internal BE module with a review-before-save flow.** Following the monolith decision, the processor source is copied into `jobtology_be.document_processor`, dependencies/native bundles ship with BE, and Python 3.12 is retained. See [the implemented library integration](../document-processing.md). A public upload API, OCR integration, LLM résumé extraction, and production import UI remain separate work.

## Evidence from the supplied processor

Inspected `/home/maxjo/Work/fedora-migration-backup-20260918-185620/Work/document-processor` at commit `7541e87`. The source checkout was left unchanged. The processor exposes:

- `DocIR.from_file(...)` for DOCX, HWPX, HWP, legacy DOC, and PDF.
- `read_document(DocumentInput(doc_ir=...))` with paragraph/run IDs, exact text, text hashes, optional page numbers, and nested table-cell text. It is paginated; callers must follow `next_start`.
- `TextAnnotation` and `render_review_html(...)` for source highlights, including exact substrings, occurrence selection, labels, and notes.
- PDF page parsing status, scan-like page detection, page geometry, and PDF annotation APIs.

The probe at [`experiments/resume_import/verify_processor.py`](../../experiments/resume_import/verify_processor.py) generated synthetic documents only. No real résumé, remote LLM, production API, or database was used. Initially verified with Python 3.14.8 and local Java 25, then verified against the copied module in an isolated BE wheel install on Python 3.12 with BE's locked dependencies. The 269 imported parser tests also pass on 3.12. The report records installed library versions. Optional `--native-smoke` loads bundled HWP converter classes and boots the legacy DOC CLI with .NET 8; it does not establish HWP/DOC file accuracy.

| Check | Measured result |
| --- | --- |
| Korean DOCX project evidence split over formatting runs | Exact paragraph quote preserved and highlighted |
| Evidence inside a DOCX table cell | Found recursively and highlighted |
| Parse identical DOCX again | Same paragraph node IDs |
| Repeated `Python Python` selection | Ambiguous quote rejected until `occurrence_index=1`; correct second span resolved |
| Invented evidence quote | Rejected as `selected_text_not_found` |
| Minimal Korean HWPX container | Text extraction and annotation passed |
| Synthetic English PDF with a text layer | Text extraction, page 1, and annotation passed |
| Image-only PDF | Page marked `pdf_scan_like`; no text extracted |
| Hidden DOCX run | Text appeared in extraction, regular HTML, and review HTML |
| Untrusted IR style string containing a quote and event attribute | The initial probe emitted event attributes. The copied renderers now escape style attributes and validate color values; the regression probe asserts no event attributes. **Raw HTML still needs a full sanitization boundary**. |
| Same test through a native DOCX font name | No event attribute emitted in this case; the IR result is not proof of a native-file exploit |

HWP and legacy DOC runtime parsing were inspected but not exercised by this synthetic probe. The HWPX sample is a minimal container, not evidence of fidelity for every real Korean résumé template. Layout quality, OCR accuracy, multilingual field extraction, and LLM precision remain unevaluated on representative résumés.

Reproduce using the monolithic backend environment:

```sh
uv sync --locked --dev
timeout 60s uv run python experiments/resume_import/verify_processor.py --out /tmp/jobtology-resume-probe
```

Outputs are synthetic DOCX/HWPX/PDF inputs, `report.json` with source spans and hashes, and `review.html`. The script is an engineering probe, not an upload handler, skill matcher, or safe renderer for arbitrary user documents. It now imports the internal BE source; no separate `document-processor` distribution is installed.

## Recommended extraction policy: rules first, optional LLM interpretation

1. Parse locally into text blocks and source locations. Show what was actually read, skipped pages, and extraction warnings.
2. Use literal dictionary/alias matching for skill mentions and conservative patterns for explicit dates and scores. Retrieve candidates from the same canonical `SkillCatalog` used by manual entry. A keyword match is an observation, never competency credit.
3. Offer **AI로 경험 해석하기** for unrecognized or ambiguous experience, with an explicit explanation of the text sent to the configured provider. Default import performs no LLM request. The user can remove contact information and private company content before this step.
4. Let the LLM propose category, canonical competency IDs, and supporting quotations from bounded sections. Validate every ID against catalog retrieval and every quote/offset against the parsed source. Reject unknown IDs and invented evidence. Preserve Korean/English source quotes verbatim; translated explanations may be separate.
5. User reviews each suggestion in the existing structured spec editor, corrects metadata, and chooses experience level. Only confirmed records enter the normal capability/version/recompute flow.

| Input | Rules can establish | Interpretation or confirmation still needed |
| --- | --- | --- |
| `Python`, `FastAPI`, `C++` | Literal skill mention with a source span | What the user actually implemented, relevant NCS competency, experience level |
| `로그인 API를 직접 구현` | Verbatim project evidence and its location | Candidate category/competencies and the user's scope |
| `TOEIC 850 / 990` | Explicit score and maximum | Test identity, date, units, and any approved equivalence policy |
| Certificate name, issuer, dates | Values explicitly present in the document | Ownership, authenticity, expiry rules, and competency equivalence |
| Employer, project, role, date range | Candidate structured fields | Responsibilities versus team contributions, overlapping periods, date precision |
| Major, enrollment, expected graduation | Explicit education statements | Confirm profile changes separately; do not overwrite existing values |

Simple ATS-style scanning is useful for fast retrieval and highlighting. It cannot reliably distinguish “배우고 싶다,” “사용하지 않았다,” team members' work, copied job descriptions, or keywords hidden in a document. LLM interpretation can help with these distinctions but must be evaluated; exact quotation alone proves that a sentence exists, not that its claim is true. Neither method authenticates a credential or measures proficiency.

Use script-aware boundaries and reviewed aliases: `Java` must not match inside `JavaScript`, `C++` punctuation must survive, and Korean particles after English names must be handled. Group evidence within its project/job section; deduplicate repeated CV summaries. Do not sum repeated mentions, double-count overlapping employment periods, or invent missing date components/score maxima. A month/year source must keep its precision and ask for missing day-level dates required by the current editor.

NCS and O*NET remain separate catalogs. A generic O*NET skill does not gain an invented NCS equivalence. Full-document translation is unnecessary for the initial flow: bilingual catalog retrieval plus optional interpretation can handle English prose while retaining the original evidence.

## Integration with current Jobtology

The shared FE `SpecManager`/`SpecEditor`, authenticated `GET /api/v1/skills`, strict capability mutation API, and partial-credit evaluator provide the confirmation and planning pieces. Import should supply drafts, not bypass those pieces.

The existing `POST /api/v1/chat/messages` spec mode is designed for short descriptions and at most three candidates. Its schema has no document locations or complete résumé field extraction. Do not silently truncate a résumé into that route. Reuse `LlmClient.complete_json`, catalog validation, and quote verification in a separate bounded import service. Résumé text must be treated as untrusted data, including text that asks the model to change instructions or make tool calls. The extraction model should have no browser, filesystem, database-writing, or arbitrary network tools.

Suggested lifecycle, **not current endpoints**:

```text
POST   /api/v1/me/resume-imports                 multipart upload -> 202, private job ID
GET    /api/v1/me/resume-imports/{id}            status, warnings, extracted sections, draft suggestions
POST   /api/v1/me/resume-imports/{id}/interpret  explicit consent + selected/redacted sections -> 202
GET    /api/v1/me/resume-imports/{id}/preview    private, sanitized, sandboxed preview
DELETE /api/v1/me/resume-imports/{id}            cancel and erase transient artifacts
```

Job states should distinguish `PARSING`, `NEEDS_OCR`, `READY`, `INTERPRETING`, `FAILED`, and `EXPIRED`. A partially scanned PDF should report skipped pages and still offer the readable sections. An empty extraction must never be reported as “no skills.”

Each suggestion needs a stable draft ID, extraction method (`RULE`/`LLM`), category, proposed fields, canonical skill candidates, warnings, and source references. A reference should include import ID, document SHA-256, parser revision, node ID, node-text hash, exact quote, occurrence index, and validated start/end offsets; page number/bounding box only when actually available. Node IDs are structural and must be scoped to a particular document version. Verify the hash before using old annotations. Define API offsets as Unicode code-point indices; FE must convert them before UTF-16 slicing or use backend-resolved annotated markup.

Current `SpecDetails.source` permits only `MANUAL`/`LLM_ASSISTED`, and its model rejects extra fields. Before production import, extend the typed FE/BE contract for document provenance rather than hiding it in unchecked JSON or pretending every imported record was manual. Keep confirmed quotations and structured metadata; retain full files only under the transient policy below. A private durable import-job store and cleanup lifecycle will need implementation; a new database job table would require its own migration. **This feasibility change adds no migrations.**

Review can save confirmed items through existing sequential versioned mutations with stable per-item idempotency keys and readback. Display per-item success/failure and resume retries without recreating saved records. If all-or-nothing batch acceptance is desired, add a transactional batch command instead. Never replace the existing spec list wholesale, merge an existing entry, change a profile field, set a goal, or grant production experience without explicit review. Existing partial-credit rules remain authoritative.

## Deployment and trust boundaries

- BE remains pinned to Python **3.12** in `.python-version`. Although the original package metadata required **3.13+**, its source and imported tests work on 3.12, so no interpreter upgrade is needed. The source is copied into BE and Python dependencies are locked there. PDF/HWP Java tools and legacy DOC .NET assemblies are bundled; Railpack requests Java 25 and .NET 8 in the same image. No workstation backup mount or separate parser package/service is needed.
- The PDF subprocess runner has no timeout, and HWP runs a JVM in-process. Run future upload parsing in bounded child processes from the same BE image, outside API request threads, with CPU, memory, output, decompression, and wall-time limits; terminate the whole process group on timeout, including Java children. Give each process a per-job scratch directory and restrict credentials/network access. This is a process boundary inside the monolith, not a separate deployment. Preserve the normal authenticated, CSRF-protected API boundary.
- Start the rollout with DOCX and text-layer PDF. HWPX is technically usable and worth adding after Korean template fixtures and archive limits are covered. Add HWP/DOC converters later. Scan-only PDF/image input needs an explicit Korean/English OCR component; local page rendering is available but is not OCR. Benchmark that component separately before promising support.
- Validate extension, signature/container structure, and size; never trust the MIME header or original filename. Reject encryption/password protection with a clear message. Enforce archive entry/uncompressed-byte and embedded-image limits before heavy parsing. Do not fetch URLs or linked assets found in résumés.
- Treat both ordinary and review HTML as untrusted. Prefer a controlled React text/annotation renderer first. A rich preview needs tag/attribute/CSS/URL sanitization plus an opaque-origin sandbox iframe without scripts or `allow-same-origin`; strip active elements, links/forms, external resources, and unsafe SVG/CSS. Use restrictive CSP and no-referrer/no-store headers. A plain CSP alone does not replace sanitization. If the parent needs to select highlights, use its own verified JSON spans in the controlled renderer; do not relax the iframe sandbox for direct DOM access.
- Detect/exclude hidden text where available and expose extraction uncertainty. The current DOCX probe shows that this needs parser/adaptor work. Keep aspirations, negated claims, team work, job requirements, and instruction-like text out of automatic acceptance. User review remains necessary even after these filters.
- Use Google-account-owned job IDs and enforce ownership on every status/preview/interpret/delete route. Initial product limits to benchmark: 10 MiB uploads, 20 PDF pages, 60 seconds parsing, 50k extracted characters, 30 suggestions, and one active parser job per account. Add separate per-account import/LLM quotas; the existing short-chat quota is not a document-work budget.
- Proposed retention: private original/IR/preview artifacts expire within 24 hours or are erased immediately when the user finishes/cancels; persist only confirmed short quotes/fields. Enforce expiry with an independent sweeper, read-time checks, crash cleanup, and account-deletion cleanup. Do not log raw documents, source text, parser stderr, or model payloads. An account deletion must also cancel pending jobs and erase their artifacts. State these rules accurately in the UI only after implemented.
- The original checkout has third-party notices but no top-level license file was found. The copied module records its source revision and local changes in `UPSTREAM.md`; existing bundled notices are preserved in the wheel/sdist. The initial probe copied no application source, and the subsequent user-authorized monolith integration copied the library into BE.

These upload and LLM boundaries follow [OWASP file-upload guidance](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) and [OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html). Product limits and retention durations above are proposed defaults, not measured capacity or implemented controls.

## Implementation order and acceptance gates

1. Private parser job + bounded DOCX/PDF extraction, owner/CSRF checks, expiry/account cleanup, literal skill observations, and safe JSON source spans. Reuse the shared editor for reviewed saving. Manual entry stays available.
2. Shared onboarding/My Info import review, source highlighting, duplicate review, version conflicts, partial-save recovery, and explicit education-field confirmation. See the [FE interaction plan](https://github.com/SMU-GoodEyeDeer/Jobtology-FE/blob/feat/functionality-improvements/docs/resume-import-plan.md).
3. Optional bounded LLM interpretation using retrieved canonical IDs and exact evidence, account quota/cost limits, and failure handling. Evaluate on synthetic Korean/English cases plus consented representative résumés: precision of competency mapping, source-span accuracy, unsupported-field invention, aspiration/team-work false positives, and usefulness versus rules alone. Do not reuse the small chat benchmark as résumé accuracy evidence.
4. Korean template coverage, HWPX, OCR, then HWP/DOC after worker runtime/format hardening and real layout tests.

Required production tests include file/container abuse, cross-account access, hidden/instruction-like text, malicious HTML/style/URL output, parser crashes/timeouts, mixed text/scan PDFs, repeated quotations, Korean/English aliases, year/month date precision, score validation, expired imports, deletion during parsing, restart cleanup, save retries/conflicts, and changed analysis hours only after confirmed input. No real résumé has been sent to an LLM as part of this assessment.
