# Document processing inside BE

The processor source is copied into `src/jobtology_be/document_processor` from
upstream commit `7541e87`. It ships as part of Jobtology BE's wheel and source
distribution. There is no dependency on an installed `document-processor`
package, workstation backup directory, or external parsing service.

## Python compatibility and installation

BE remains on Python **3.12**. The source's declared 3.13 minimum did not require
a language backport: all 54 modules parse as 3.12, and the 269 imported automated
tests pass with BE's locked Pydantic and parser dependencies. The four optional
upstream demo tests are outside this integration; they test a separate LLM demo
script, not the parsing library. No original project files were modified.

```sh
uv sync --locked --dev
uv run pytest tests/document_processing
uv run python experiments/resume_import/verify_processor.py --out /tmp/resume-probe
```

Normal third-party dependencies remain: `python-docx`, Pydantic, `pypdf`,
`pypdfium2`, and JPype. These are declared in `pyproject.toml` and pinned by
`uv.lock`; the copied processor itself is not downloaded from a registry.

## Internal API

```python
from jobtology_be.document_processor import (
    DocIR, DocumentInput, TextAnnotation, read_document, render_review_html,
)

doc = DocIR.from_file("resume.docx")
source = DocumentInput(doc_ir=doc)
first_page = read_document(document=source, start=0, limit=50)
paragraph = first_page.paragraphs[0]
review = render_review_html(document=source, annotations=[TextAnnotation(
    target_kind="paragraph", target_id=paragraph.node_id,
    selected_text=paragraph.text, label="사용자 확인 필요",
)])
```

Follow `next_start` when reading long documents. Both file paths and bytes-backed
`DocumentInput` are supported by the library. Client-supplied filesystem paths
must never be passed directly to these internal functions by a future API.

## One deployment image

| Format | Runtime |
| --- | --- |
| DOCX, HWPX | Python dependencies only |
| PDF with a text layer | Java + bundled OpenDataLoader JAR; PDFium provides probing/preview |
| HWP | Java 25/JPype + bundled HWP-to-HWPX converter JARs |
| Word 97–2003 DOC | .NET 8 + bundled DocSharp CLI assemblies |

The HWP converter JAR contains Java class version 69, which requires Java 25.
PDF alone needs Java 11+. `railpack.json` requests Java 25 and .NET 8 through Mise and retains the platform's
default runtime Apt packages while adding ICU, fontconfig and `curl`. Coolify's
HTTP healthcheck runs inside the final image and requires `curl` (or `wget`);
installing a client only on the build/deployment server is insufficient. `.mise.toml` uses
isolated .NET installs so all runtime files stay in `/mise/installs`, which
Railpack carries into the final image. The API provider and start command remain
Python/Uvicorn. These are tools in the same image, not additional application
services. See the official [Railpack package configuration](https://railpack.com/guides/installing-packages)
and [Mise .NET installation settings](https://mise.jdx.dev/lang/dotnet.html).

For local development, install Java and .NET 8 only for the formats you exercise;
DOCX/HWPX work without them. Missing runtimes produce explicit parser errors.
The existing converter override environment variables remain available for
operators; they are not user-controlled request parameters.

Native bundles and their notices are included explicitly in Hatch artifacts.
After building, validate the installed wheel outside the source checkout:

```sh
uv build
# Install dist/jobtology_be-*.whl into a fresh environment, then run:
python -m jobtology_be.document_processor.runtime_check
```

The runtime check reports available converters and can require them with
`--require-all`. CI also runs the synthetic DOCX/HWPX/text-PDF/scan-PDF probe
against the installed wheel, so source-relative paths cannot hide missing
package assets. The probe's `--native-smoke` option additionally loads the three
HWP converter classes in a JVM and boots the bundled DOC CLI to its usage response.
This checks native packaging/startup, rather than claiming HWP/DOC file accuracy.
No real résumé or LLM is involved in that check.

## Résumé import remains a separate product step

This integrates the library, dependencies, native bundles, and runtime image
configuration. It adds no public upload endpoint, persistent import jobs, OCR,
or automatic profile writes. The parser's rich HTML is untrusted, and scan-only
PDF pages are classified/skipped rather than OCR'd. Hidden text and unsafe IR
style values remain documented limitations of the copied library.

Use a bounded child process from the same BE image for future upload jobs;
monolithic packaging does not require running untrusted document parsing in an
API request thread. Add resource limits, strict file/container validation,
sanitized/controlled preview, per-account ownership/quotas, expiry cleanup, and
reviewed capability/profile writes as described in the
[résumé integration design](plans/resume-import-feasibility.md).
