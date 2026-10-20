from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


from jobtology_be.document_processor.pdf.page_images import render_scan_like_pdf_pages
from jobtology_be.document_processor.pdf.parsing import PageProfile, PdfProfile


class PdfPageImagesTests(unittest.TestCase):
    def test_render_scan_like_pdf_pages_returns_only_scan_like_pages_as_pngs(self) -> None:
        profile = PdfProfile(
            page_count=2,
            avg_chars_per_page=25.0,
            normal_text_ratio=0.8,
            text_readable=True,
            text_readable_page_ratio=0.5,
            page_profiles=[
                PageProfile(
                    page_number=1,
                    char_count=0,
                    normal_text_ratio=0.0,
                    replacement_char_ratio=0.0,
                    text_readable=False,
                    image_area_ratio=1.0,
                    image_area_in_content_ratio=1.0,
                    page_width_pt=300.0,
                    page_height_pt=200.0,
                ),
                PageProfile(
                    page_number=2,
                    char_count=50,
                    normal_text_ratio=0.8,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.1,
                    image_area_in_content_ratio=0.1,
                    page_width_pt=300.0,
                    page_height_pt=200.0,
                ),
            ],
        )

        class FakeBitmap:
            width = 6
            height = 4
            stride = 18
            mode = "BGR"
            n_channels = 3
            buffer = bytes([0, 0, 255]) * (width * height)

            def close(self) -> None:
                return None

        class FakePage:
            def render(self, *, scale: float, grayscale: bool) -> FakeBitmap:
                self.scale = scale
                self.grayscale = grayscale
                return FakeBitmap()

        class FakePdfDocument:
            def __init__(self, path: str) -> None:
                self.path = path
                self.pages = [FakePage(), FakePage()]

            def __getitem__(self, index: int) -> FakePage:
                return self.pages[index]

            def close(self) -> None:
                return None

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.7\n%fake")

            with patch("jobtology_be.document_processor.pdf.page_images.probe_pdf", return_value=profile), patch.dict(
                sys.modules,
                {"pypdfium2": SimpleNamespace(PdfDocument=lambda path: FakePdfDocument(path))},
            ):
                pages = render_scan_like_pdf_pages(pdf_path, dpi=216)

        self.assertEqual(list(pages), [1])
        rendered_page = pages[1]
        self.assertEqual(rendered_page.page_number, 1)
        self.assertEqual(rendered_page.mime_type, "image/png")
        self.assertEqual(rendered_page.width_px, 6)
        self.assertEqual(rendered_page.height_px, 4)
        self.assertEqual(rendered_page.dpi, 216)
        self.assertEqual(rendered_page.page_width_pt, 300.0)
        self.assertEqual(rendered_page.page_height_pt, 200.0)
        self.assertTrue(rendered_page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(int.from_bytes(rendered_page.png_bytes[16:20], "big"), 6)
        self.assertEqual(int.from_bytes(rendered_page.png_bytes[20:24], "big"), 4)
        self.assertTrue(rendered_page.as_data_url().startswith("data:image/png;base64,"))

        asset = rendered_page.as_image_asset("sample-page-1.png")
        self.assertEqual(asset.mime_type, "image/png")
        self.assertEqual(asset.filename, "sample-page-1.png")
        self.assertEqual(asset.intrinsic_width_px, 6)
        self.assertEqual(asset.intrinsic_height_px, 4)
        self.assertEqual(asset.bytes_data(), rendered_page.png_bytes)

        bbox = rendered_page.pixel_bbox_to_doc_bbox(
            x0_px=1,
            y0_px=1,
            x1_px=3,
            y1_px=3,
        )
        self.assertEqual(bbox.left_pt, 50.0)
        self.assertEqual(bbox.right_pt, 150.0)
        self.assertEqual(bbox.top_pt, 150.0)
        self.assertEqual(bbox.bottom_pt, 50.0)

    def test_render_scan_like_pdf_pages_returns_empty_dict_when_no_scan_like_pages(self) -> None:
        profile = PdfProfile(
            page_count=1,
            avg_chars_per_page=50.0,
            normal_text_ratio=0.8,
            text_readable=True,
            text_readable_page_ratio=1.0,
            page_profiles=[
                PageProfile(
                    page_number=1,
                    char_count=50,
                    normal_text_ratio=0.8,
                    replacement_char_ratio=0.0,
                    text_readable=True,
                    image_area_ratio=0.1,
                    image_area_in_content_ratio=0.1,
                    page_width_pt=300.0,
                    page_height_pt=200.0,
                ),
            ],
        )

        with patch("jobtology_be.document_processor.pdf.page_images.probe_pdf", return_value=profile):
            pages = render_scan_like_pdf_pages("structured.pdf")

        self.assertEqual(pages, {})


if __name__ == "__main__":
    unittest.main()
