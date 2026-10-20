"""Render scan-like PDF pages for OCR/VLM processing."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import struct
import zlib

from ..models import BoundingBox, ImageAsset
from .parsing import PageClass, decide_page, probe_pdf

_DEFAULT_SCAN_PAGE_DPI = 216
_PNG_MIME_TYPE = "image/png"


@dataclass(frozen=True, slots=True)
class RenderedScanPage:
    """Full-page PNG artifact for a scan-like PDF page."""

    page_number: int
    png_bytes: bytes
    mime_type: str
    width_px: int
    height_px: int
    dpi: int
    page_width_pt: float
    page_height_pt: float

    def as_data_url(self) -> str:
        data_base64 = base64.b64encode(self.png_bytes).decode("ascii")
        return f"data:{self.mime_type};base64,{data_base64}"

    def as_image_asset(self, filename: str | None = None) -> ImageAsset:
        return ImageAsset.from_bytes(
            data=self.png_bytes,
            mime_type=self.mime_type,
            filename=filename,
            intrinsic_width_px=self.width_px,
            intrinsic_height_px=self.height_px,
        )

    def pixel_bbox_to_doc_bbox(
        self,
        *,
        x0_px: float,
        y0_px: float,
        x1_px: float,
        y1_px: float,
    ) -> BoundingBox:
        """Convert top-left-origin PNG pixel coordinates to DocIR page coordinates."""
        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("rendered page pixel dimensions must be positive")
        if self.page_width_pt <= 0 or self.page_height_pt <= 0:
            raise ValueError("rendered page point dimensions must be positive")

        left_px, right_px = sorted((x0_px, x1_px))
        top_px, bottom_px = sorted((y0_px, y1_px))
        scale_x = self.page_width_pt / self.width_px
        scale_y = self.page_height_pt / self.height_px
        return BoundingBox(
            left_pt=left_px * scale_x,
            bottom_pt=self.page_height_pt - (bottom_px * scale_y),
            right_pt=right_px * scale_x,
            top_pt=self.page_height_pt - (top_px * scale_y),
        )


@dataclass(frozen=True, slots=True)
class _RenderedBitmap:
    width_px: int
    height_px: int
    stride: int
    pixels: bytes
    mode: str
    n_channels: int


def render_scan_like_pdf_pages(
    pdf_path: str | Path,
    *,
    dpi: int = _DEFAULT_SCAN_PAGE_DPI,
) -> dict[int, RenderedScanPage]:
    """Render pages classified as scan-like to full-page PNG artifacts."""
    if dpi <= 0:
        raise ValueError("dpi must be positive")

    source_path = Path(pdf_path)
    profile = probe_pdf(source_path)
    if profile is None:
        raise RuntimeError("PDF probe failed before scan page rendering.")

    scan_page_profiles = [
        page_profile
        for page_profile in profile.page_profiles
        if decide_page(page_profile).page_class == PageClass.SCAN_LIKE
    ]
    if not scan_page_profiles:
        return {}

    import pypdfium2 as pdfium

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(str(source_path))
    try:
        rendered_pages: dict[int, RenderedScanPage] = {}
        for page_profile in scan_page_profiles:
            pdf_page = doc[page_profile.page_number - 1]
            bitmap = pdf_page.render(scale=scale, grayscale=False)
            try:
                rendered_bitmap = _rendered_bitmap_from_pdfium(bitmap)
                page_width_pt = _page_dimension_pt(
                    probed_value=page_profile.page_width_pt,
                    page=pdf_page,
                    getter_name="get_width",
                    fallback_px=rendered_bitmap.width_px,
                    dpi=dpi,
                )
                page_height_pt = _page_dimension_pt(
                    probed_value=page_profile.page_height_pt,
                    page=pdf_page,
                    getter_name="get_height",
                    fallback_px=rendered_bitmap.height_px,
                    dpi=dpi,
                )
                rendered_pages[page_profile.page_number] = RenderedScanPage(
                    page_number=page_profile.page_number,
                    png_bytes=_encode_bitmap_as_png(rendered_bitmap),
                    mime_type=_PNG_MIME_TYPE,
                    width_px=rendered_bitmap.width_px,
                    height_px=rendered_bitmap.height_px,
                    dpi=dpi,
                    page_width_pt=page_width_pt,
                    page_height_pt=page_height_pt,
                )
            finally:
                bitmap.close()
        return rendered_pages
    finally:
        doc.close()


def _rendered_bitmap_from_pdfium(bitmap) -> _RenderedBitmap:  # noqa: ANN001
    mode = getattr(bitmap, "mode", "BGR")
    return _RenderedBitmap(
        width_px=bitmap.width,
        height_px=bitmap.height,
        stride=bitmap.stride,
        pixels=bytes(bitmap.buffer),
        mode=mode,
        n_channels=getattr(bitmap, "n_channels", _channel_count_for_mode(mode)),
    )


def _channel_count_for_mode(mode: str) -> int:
    if mode in {"BGRA", "RGBA"}:
        return 4
    return 3


def _page_dimension_pt(
    *,
    probed_value: float | None,
    page,  # noqa: ANN001
    getter_name: str,
    fallback_px: int,
    dpi: int,
) -> float:
    if probed_value is not None:
        return float(probed_value)

    getter = getattr(page, getter_name, None)
    if callable(getter):
        page_value = getter()
        if page_value:
            return float(page_value)

    return fallback_px * 72.0 / dpi


def _encode_bitmap_as_png(bitmap: _RenderedBitmap) -> bytes:
    raw_rows = bytearray()
    color_type = 2
    for y in range(bitmap.height_px):
        raw_rows.append(0)
        row_offset = y * bitmap.stride
        for x in range(bitmap.width_px):
            idx = row_offset + (x * bitmap.n_channels)
            if bitmap.mode == "BGR":
                blue, green, red = bitmap.pixels[idx: idx + 3]
                raw_rows.extend((red, green, blue))
            elif bitmap.mode == "BGRA":
                blue, green, red, alpha = bitmap.pixels[idx: idx + 4]
                raw_rows.extend((red, green, blue, alpha))
                color_type = 6
            elif bitmap.mode == "RGB":
                raw_rows.extend(bitmap.pixels[idx: idx + 3])
            elif bitmap.mode == "RGBA":
                raw_rows.extend(bitmap.pixels[idx: idx + 4])
                color_type = 6
            else:
                blue, green, red = bitmap.pixels[idx: idx + 3]
                raw_rows.extend((red, green, blue))

    return _encode_png(
        width=bitmap.width_px,
        height=bitmap.height_px,
        color_type=color_type,
        raw_scanlines=bytes(raw_rows),
    )


def _encode_png(*, width: int, height: int, color_type: int, raw_scanlines: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", ihdr),
            chunk(b"IDAT", zlib.compress(raw_scanlines)),
            chunk(b"IEND", b""),
        ]
    )


__all__ = ["RenderedScanPage", "render_scan_like_pdf_pages"]
