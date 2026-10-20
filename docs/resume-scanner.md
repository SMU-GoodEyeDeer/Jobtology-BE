# Résumé scanner

Implemented on `feat/functionality-improvements` in the monolithic BE and the paired FE. It is available through **내 스펙 → 이력서에서 가져오기**, both during onboarding and later profile edits. It does not require a career goal.

The user uploads a document, reviews extracted text and literal catalog matches, and opens a suggestion in the shared spec editor. They must choose their own experience level and confirm each save. Only then does the existing versioned capability API write a self-reported spec. The FE fetches its full matching feedback immediately. A new analysis applies the spec; an existing roadmap is not automatically replaced.

## API

All endpoints require the existing account session. POSTs require its CSRF token and trusted origin. Responses use `Cache-Control: no-store`.

- `GET /api/v1/me/resume/status`: actual runtime-supported `formats`, `max_bytes`, `max_pdf_pages`, `parse_timeout_seconds`, `ai_available`, `ai_provider`, `retention: REQUEST_ONLY`.
- `POST /api/v1/me/resume/scan?format=docx[&occupation_id=...]`: raw binary request body (`application/octet-stream`), **no multipart or filename**. Returns document SHA-256, parser revision, text blocks, bounded suggestions, warning codes and skipped PDF page numbers. No profile mutation and no AI call.
- `POST /api/v1/me/resume/interpret`: JSON `{text, occupation_id?}` with a selected, user-reviewed passage of at most 2,000 Unicode code points. Explicit opt-in only; it calls the existing configured LLM client. Returns canonical skill IDs plus exact source quotes and validated code-point offsets. Invalid IDs/quotes/spans are discarded, with `rejected_count`.

The scanner and interpretation share the existing PostgreSQL account quota with chat (20 requests/minute/account). Logout/login does not reset it. Scans also allow one active upload/parse per account and two total per BE filesystem, shared across web workers using file locks. These slots do not coordinate separate hosts; each host has its own capacity limit.

## Formats and bounds

DOCX and HWPX use Python parsers. Text PDF requires Java 11+ and the bundled ODL converter; legacy HWP requires Java 25/JPype and its bundled converter; legacy DOC requires .NET 8 and its bundle. The status endpoint only advertises discovered runtimes. The monolith Railpack configuration installs Java 25/.NET 8. Runtime discovery does not guarantee every template can be read.

- Upload: 10 MiB, 30-second streaming deadline, magic/container checks.
- PDF: at most 20 pages; reject encrypted input. Image-only/unreadable pages show `OCR_REQUIRED` and page numbers; **OCR is not implemented**.
- Archives: at most 1,000 members / 40 MiB expanded / 15 MiB per member / 200:1 compression ratio; reject path traversal, encryption, macro bundles, XML DTD/entity declarations (including UTF-16/32 NUL interleaving).
- Child parser: 60-second wall deadline, 45 CPU seconds per process, 4 GiB address-space limit per process, 32 MiB output-file limit, 128 open descriptors, no core dump. Native heaps are restricted. The whole process group is killed on completion, timeout or client cancellation. An independent child alarm also covers a web worker crash.
- Results: at most 50,000 text code points / 500 blocks / 1,800 code points per block / 30 literal suggestions; truncation is visible. Only known PDF physical page numbers are shown; DOCX/HWPX pages are not invented.

Synthetic Korean DOCX (including tables/hidden styles), HWPX, text PDF and image-only PDF are exercised in tests. HWP/DOC native assets/startup are covered by the existing installed-wheel smoke checks; real legacy résumé-template accuracy is not established by those checks.

## Temporary data and preview

The source filename is never sent. The server writes to a private directory under the system temporary directory with an opaque generated name, launches the processor in a separate process, returns bounded JSON and removes all files in `finally`. No original file, IR, preview or import job is added to the database. Active locks prevent crash cleanup from deleting live requests. A lifespan sweeper runs every minute and removes unlocked leftovers older than 15 minutes, including after a restart. There are only three reusable capacity lock files, not a growing file per account/session. Normal cancellation disconnects stop processing and erase the request directory.

The child receives a restricted environment without database/LLM credentials. These are process/resource boundaries, not a full OS sandbox or malware scanner; parsing remains part of the trusted BE deployment. Keep parser and native runtime dependencies patched.

The FE holds the returned text only in component memory. Finish, navigation, logout/account deletion and component unmount clear that preview; no document content enters URLs, analytics, localStorage or console logs. Confirmed spec quotes are ordinary account data and are removed by existing account deletion. Original files are not retained for future previews.

Document-provided HTML, styles, images, links and scripts are never inserted into the page. React renders plain JSON text, with application-controlled highlight markup. Explicit DOCX hidden/deleted runs and hidden character/paragraph styles are excluded. Extraction can still differ from visible layout and reading order; the user must review it.

## Meaning of suggestions

Literal matching uses NCS/O*NET catalog labels/aliases and ASCII token boundaries (JavaScript is not Java). Suggestions are grouped per text block, not one spec per keyword. A few explicitly written language-test scores are parsed; no dates, issuer, degree, credential validity or proficiency are invented. Year/month dates remain in the quotation for user review, rather than becoming invented day-level dates. Header context can suggest a category, but users can change it. Bare headings do not count as qualifications.

Keyword occurrences may describe aspirations, negations, third-party experience or job requirements. They are **observations for review**, not competency credit. The AI prompt explicitly excludes those cases, but users still review every suggestion. O*NET IDs remain distinct and do not imply an NCS crosswalk.

Optional AI sends **only** the edited selected passage to the existing configured provider, not the whole document. The FE masks common email/phone patterns, displays the exact outgoing text and provider, and requires an explicit acknowledgment. This is not comprehensive de-identification: users must remove names and private/company information. Upload, ordinary review and rules-only save work without AI. AI quota/unavailability does not block manual selection. Real-model résumé accuracy has not been benchmarked by the fake-client tests.

Confirmed specs use `details.source: DOCUMENT_IMPORT` and a strict `document_evidence` object: document fingerprint, parser revision, block ID/text hash, exact original quote, half-open Unicode code-point span and method (`KEYWORD`, `AI_ASSISTED`, `SELECTED_TEXT`). This is user-supplied provenance, **not cryptographic verification**. No arbitrary internal completion fields or verified-credential flags are accepted. Edited/redacted AI quotes are re-anchored only when exact and unambiguous; repeated spans retain explicit offsets when the original passage is unchanged. The FE uses code-point slicing, not UTF-16 offsets.

Experience level is mandatory and never inferred from a document. The existing modeled planning weights and evidence rules apply only after confirmation. Duplicate evidence is not summed. The current version does not automatically modify education/profile fields, merge existing specs, perform OCR, or verify credentials.

## Deployment

No SQL migration or new secret is needed for this scanner. Deploy the BE before the FE; the existing LLM configuration enables optional interpretation. The temporary directory must be writable by the BE user and private to that service. The normal Java 25/.NET 8 image configuration must be included to advertise native formats. This branch has not been deployed by the implementation task.
