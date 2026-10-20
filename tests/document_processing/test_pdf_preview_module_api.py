from __future__ import annotations

import importlib
import unittest



class PdfPreviewModuleApiTests(unittest.TestCase):
    def test_pdf_root_does_not_export_internal_parsing_helpers(self) -> None:
        module = importlib.import_module("jobtology_be.document_processor.pdf")

        self.assertEqual(getattr(module, "__all__", []), [])
        self.assertFalse(hasattr(module, "parse_pdf_to_doc_ir"))
        self.assertFalse(hasattr(module, "PdfParseConfig"))
        self.assertFalse(hasattr(module, "export_pdf_local_outputs"))

    def test_preview_submodules_are_importable(self) -> None:
        for module_name in (
            "jobtology_be.document_processor.pdf.preview.models",
            "jobtology_be.document_processor.pdf.preview.analyze",
            "jobtology_be.document_processor.pdf.preview.shared",
            "jobtology_be.document_processor.pdf.preview.context",
            "jobtology_be.document_processor.pdf.preview.normalize",
        ):
            module = importlib.import_module(module_name)
            self.assertIsNotNone(module)

    def test_preview_root_exports_minimal_public_api(self) -> None:
        module = importlib.import_module("jobtology_be.document_processor.pdf.preview")
        expected_exports = {
            "enrich_pdf_doc_ir",
        }
        forbidden_exports = {
            "PdfLayoutRegion",
            "PdfPreviewVisualBlockCandidate",
            "PdfPreviewContext",
            "PdfPreviewTableContext",
            "PdfPreviewVisualPrimitive",
            "build_pdf_preview_context",
            "render_pdf_preview_html",
            "prepare_pdf_for_html",
        }

        self.assertEqual(set(getattr(module, "__all__", [])), expected_exports)
        for export_name in expected_exports:
            self.assertTrue(hasattr(module, export_name))
        for export_name in forbidden_exports:
            self.assertFalse(hasattr(module, export_name))
