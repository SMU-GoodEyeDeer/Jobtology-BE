"""Unified native range extractor interface.

Materialize a contiguous range of a document's paragraphs as a standalone native
file, dispatching by document type. DOC and HWP sources are converted first. Mirrors
``structured_mapping_exporter.export_structured_mapping``: the format-specific
extractors are the implementation; this is the single general entry point.

Ranges are addressed with the same ``node_id`` values a parsed ``DocIR`` exposes.
Document splitting is one caller (iterate ranges → one file each), but the
operation itself is use-case agnostic — chapter extraction, excerpts, and
selection-based redaction are equally served.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Literal

from ..io_utils import TemporarySourcePath, infer_doc_type
from .doc_converter import convert_doc_to_docx_bytes
from .docx_range_extractor import extract_docx_ranges
from .hwpx_range_extractor import extract_hwpx_ranges

DocType = Literal["auto", "hwp", "hwpx", "doc", "docx", "pdf"]


def extract_ranges(
    source: str | Path | bytes | BinaryIO,
    ranges: list[tuple[str, str]],
    *,
    doc_type: DocType = "auto",
) -> list[bytes]:
    """Extract many node-id ranges from one document as standalone native files.

    Args:
        source: a DOC/HWP/HWPX/DOCX path, bytes, or binary file object.
        ranges: ``(start_node_id, end_node_id)`` pairs, each inclusive; a range may
            span section breaks.
        doc_type: ``"auto"`` infers from the source; otherwise force the format.

    Returns one native byte string per range, in input order. HWP ranges are
    returned as HWPX and DOC ranges as DOCX. The source is parsed once regardless
    of how many ranges are requested.
    """
    resolved = infer_doc_type(source, doc_type)

    if resolved == "hwpx":
        return extract_hwpx_ranges(source, ranges)

    if resolved == "docx":
        return extract_docx_ranges(source, ranges)

    if resolved == "doc":
        with TemporarySourcePath(source, suffix=".doc") as source_path:
            docx_bytes = convert_doc_to_docx_bytes(source_path)
        return extract_docx_ranges(docx_bytes, ranges)

    if resolved == "hwp":
        from .hwp_converter import convert_hwp_to_hwpx_bytes

        if not isinstance(source, (str, Path)):
            raise TypeError("HWP range extraction currently requires a filesystem path.")
        hwpx_bytes = convert_hwp_to_hwpx_bytes(source)
        return extract_hwpx_ranges(hwpx_bytes, ranges)

    if resolved == "pdf":
        raise NotImplementedError(
            "PDF native range extraction is not supported — slice the source PDF directly."
        )

    raise ValueError(f"unsupported document type for range extraction: {resolved!r}")


def extract_range(
    source: str | Path | bytes | BinaryIO,
    *,
    doc_type: DocType = "auto",
    start_node_id: str,
    end_node_id: str,
) -> bytes:
    """Extract a single contiguous node-id range as a standalone native file.

    Thin wrapper over :func:`extract_ranges` for the one-range case.
    """
    return extract_ranges(source, [(start_node_id, end_node_id)], doc_type=doc_type)[0]


__all__ = ["DocType", "extract_range", "extract_ranges"]
