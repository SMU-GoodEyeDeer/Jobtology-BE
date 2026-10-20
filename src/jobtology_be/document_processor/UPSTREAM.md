# Source provenance

Copied into Jobtology BE on 2026-10-20 at the repository owner's request from
`document-processor`, commit `7541e87` (version 0.1.0). This is internal source
under `jobtology_be.document_processor`, not an installed `document-processor`
distribution or an independent service. The original checkout is unchanged.

The source package declared Python >=3.13. Its 54 Python modules parse on Python
3.12 and the automated parser suite passes on 3.12, so BE retains its existing
Python 3.12 target. Runtime dependencies are declared/locked in BE's project.

Local integration changes:

- Module references, logger names, dynamic diagram paths, and regression-test
  patch targets use the BE namespace. Removed unused imports reported by the
  backend lint tool. Parser behavior and public parser functions are retained.
- The copied component and its tests retain upstream formatting, with a scoped
  Ruff configuration that still checks syntax, undefined names, and unused
  imports. The rest of BE keeps its existing lint policy. The existing early
  logger initialization is preserved with an explicit E402 exemption.
- Imported 269 regression tests; three example-file-dependent cases now use
  generated synthetic DOCX data instead of copying local sample documents.
  The external LLM demo script/test and manual notebooks are not part of this
  integration. Optional diagram generation still requires Erdantic/Graphviz.
- Bundled converter JARs, .NET assemblies, runtime manifests, and existing
  third-party notices are preserved and included in BE's wheel and sdist.

The source checkout did not contain a top-level license file or project license
metadata. No replacement license is asserted here. Existing third-party notices
remain under `core/vendor/doc2docx/`; JAR-contained license/notice metadata stays
inside the original JARs. Preserve these notices on future changes.

See `docs/document-processing.md` in the BE repository for usage, runtime needs,
packaging validation, and the remaining résumé-import work.

The résumé integration also preserves existing paragraph source text during PDF anchor canonicalization when ODL spans omit characters, and retains page-layout metadata when page-count/child hints are absent. Regression tests cover punctuation preservation and layout-only page records.
