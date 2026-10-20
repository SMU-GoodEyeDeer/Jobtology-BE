"""Unified structured mapping exporter interface."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TYPE_CHECKING

from ..io_utils import TemporarySourcePath, infer_doc_type
from .doc_converter import convert_doc_to_docx_bytes
from .docx_structured_exporter import export_docx_structured_mapping
from .hwp_converter import convert_hwp_to_hwpx_bytes
from .hwpx_structured_exporter import export_hwpx_structured_mapping

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument
    from ..hwpx import HwpxDocument


DocType = Literal["auto", "hwp", "hwpx", "doc", "docx", "pdf"]


def export_structured_mapping(
    source: "HwpxDocument | DocxDocument | str | Path | bytes",
    *,
    doc_type: DocType = "auto",
    skip_empty: bool = False,
    include_tables: bool = True,
) -> dict[str, str]:
    """Export structural run mapping for DOC/HWP/HWPX/DOCX."""
    resolved = infer_doc_type(source, doc_type)

    if resolved == "pdf":
        raise NotImplementedError("PDF structured mapping export is not implemented yet.")

    if resolved == "hwp":
        if not isinstance(source, (str, Path)):
            raise TypeError("HWP conversion currently requires a filesystem path.")
        hwpx_bytes = convert_hwp_to_hwpx_bytes(source)
        return export_hwpx_structured_mapping(hwpx_bytes, skip_empty=skip_empty)

    if resolved == "doc":
        with TemporarySourcePath(source, suffix=".doc") as source_path:
            docx_bytes = convert_doc_to_docx_bytes(source_path)
        return export_docx_structured_mapping(
            docx_bytes,
            include_tables=include_tables,
            skip_empty=skip_empty,
        )

    if resolved == "hwpx":
        return export_hwpx_structured_mapping(source, skip_empty=skip_empty)

    return export_docx_structured_mapping(
        source,
        include_tables=include_tables,
        skip_empty=skip_empty,
    )


__all__ = ["DocType", "export_structured_mapping"]
