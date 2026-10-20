"""DOCX native range extractor.

Materialize a contiguous range of top-level body blocks from an existing DOCX
document as a standalone DOCX byte string. Unlike HWPX (one XML file per section),
a DOCX keeps all body content in a single ``word/document.xml``; the section
layout lives in inline ``<w:sectPr>`` elements. So extraction is just: keep the
paragraphs/tables in range, drop the rest, and let python-docx re-package the
(preserved) styles, numbering, headers/footers, media, and relationships.

Node identity matches the DocIR / parser scheme: each top-level body block
(paragraph or table) is ``s1.p{n}`` with a shared 1-based counter, so callers can
address a range with the same ``node_id`` values a parsed ``DocIR`` exposes.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from typing import TYPE_CHECKING, BinaryIO, Iterator
import zipfile

from ..models import _stable_node_id

_DOCUMENT_XML = "word/document.xml"
_DOCUMENT_RELS = "word/_rels/document.xml.rels"
_MEDIA_TARGET_RE = re.compile(r'Target="(?:\.\./)?(media/[^"]+)"')
_IMAGE_REL_RE = re.compile(
    r'<Relationship\s+[^>]*?Id="(?P<id>[^"]+)"[^>]*?Target="(?:\.\./)?(?P<target>media/[^"]+)"[^>]*/>'
)

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument


def _coerce_to_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    read = getattr(source, "read", None)
    if callable(read):
        return read()
    raise TypeError(f"Unsupported DOCX source type: {type(source)!r}")


def _paragraph_node_id(block_index: int) -> str:
    """Node id for a top-level body block (paragraph or table), 1-based."""
    return _stable_node_id("paragraph", f"s1.p{block_index}")


def _iter_body_blocks(doc: "DocxDocument") -> Iterator[tuple[int, object]]:
    """Yield ``(block_index, element)`` for each top-level paragraph/table.

    Mirrors the DocIR parser: the counter advances for paragraphs and tables
    alike, and non-block body children (notably the trailing ``<w:sectPr>``) are
    skipped so page setup survives untouched.
    """
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    block_index = 0
    for child in doc.element.body.iterchildren():
        if isinstance(child, (CT_P, CT_Tbl)):
            block_index += 1
            yield block_index, child


def _local_name(element) -> str:
    return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""


def _paragraph_sect_pr(paragraph_el):
    from docx.oxml.ns import qn

    p_pr = paragraph_el.find(qn("w:pPr"))
    return p_pr.find(qn("w:sectPr")) if p_pr is not None else None


def _sect_pr_for_range_end(body, end_index: int):
    """Clone the section properties that apply to the range's final block."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    blocks_seen = 0
    body_sect_pr = None
    for child in body.iterchildren():
        if isinstance(child, (CT_P, CT_Tbl)):
            blocks_seen += 1
            if blocks_seen >= end_index and isinstance(child, CT_P):
                sect_pr = _paragraph_sect_pr(child)
                if sect_pr is not None:
                    return deepcopy(sect_pr)
            continue
        if _local_name(child) == "sectPr":
            body_sect_pr = child
            if blocks_seen >= end_index:
                return deepcopy(child)
    return deepcopy(body_sect_pr) if body_sect_pr is not None else None


def _replace_body_sect_pr(body, sect_pr) -> None:
    if sect_pr is None:
        return
    for child in list(body.iterchildren()):
        if _local_name(child) == "sectPr":
            body.remove(child)
    body.append(sect_pr)


def _trim_body_to_range(body, start_index: int, end_index: int) -> None:
    """Remove body content outside the block range ``[start_index, end_index]``.

    Paragraphs/tables are removed by their block index. Body-level non-block content
    (notably a ``<w:sdt>`` wrapping a table of contents) carries no block index, so
    it is removed by position — kept only when interleaved between kept blocks. The
    trailing ``<w:sectPr>`` (page setup) is always kept.
    """
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P

    blocks_seen = 0
    to_remove = []
    for child in list(body.iterchildren()):
        if isinstance(child, (CT_P, CT_Tbl)):
            blocks_seen += 1
            if blocks_seen < start_index or blocks_seen > end_index:
                to_remove.append(child)
            continue
        if _local_name(child) == "sectPr":
            continue  # page setup — keep
        # sdt / other stray body content: keep only if it sits between kept blocks.
        if blocks_seen < start_index or blocks_seen >= end_index:
            to_remove.append(child)
    for child in to_remove:
        body.remove(child)


def _prune_docx_media(docx_bytes: bytes) -> bytes:
    """Drop media files no longer referenced after the body was trimmed.

    A media file is kept if any live relationship still points to it — a body
    relationship whose ``rId`` remains in the trimmed ``word/document.xml``, or any
    relationship owned by another preserved part (headers/footers). Dead image
    relationships are also removed from ``word/_rels/document.xml.rels``.
    """
    with zipfile.ZipFile(BytesIO(docx_bytes)) as source:
        names = source.namelist()
        if _DOCUMENT_RELS not in names or _DOCUMENT_XML not in names:
            return docx_bytes

        document_xml = source.read(_DOCUMENT_XML).decode("utf-8", "replace")
        used_rids = set(re.findall(r'r:(?:embed|id|link)="([^"]+)"', document_xml))
        doc_rels = source.read(_DOCUMENT_RELS).decode("utf-8", "replace")

        image_rels = [(m.group("id"), f"word/{m.group('target')}") for m in _IMAGE_REL_RE.finditer(doc_rels)]
        dead_rids = {rid for rid, _target in image_rels if rid not in used_rids}
        live_targets = {target for rid, target in image_rels if rid in used_rids}
        dead_media = {target for rid, target in image_rels if rid in dead_rids} - live_targets

        # Keep any media a preserved part (header/footer/...) still references.
        for name in names:
            if not name.endswith(".rels") or name == _DOCUMENT_RELS:
                continue
            content = source.read(name).decode("utf-8", "replace")
            for target in _MEDIA_TARGET_RE.findall(content):
                dead_media.discard(f"word/{target}")

        if not dead_rids and not dead_media:
            return docx_bytes

        new_rels = doc_rels
        for rid in dead_rids:
            new_rels = re.sub(rf'<Relationship\s+[^>]*?Id="{re.escape(rid)}"[^>]*/>', "", new_rels)

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                if info.filename in dead_media:
                    continue
                if info.filename == _DOCUMENT_RELS:
                    target.writestr(info, new_rels.encode("utf-8"))
                    continue
                target.writestr(info, source.read(info.filename))
    return buffer.getvalue()


def _resolve_range(
    node_to_index: dict[str, int],
    start_node_id: str,
    end_node_id: str,
) -> tuple[int, int]:
    start = node_to_index.get(start_node_id)
    end = node_to_index.get(end_node_id)
    missing = [
        node_id
        for node_id, index in ((start_node_id, start), (end_node_id, end))
        if index is None
    ]
    if missing:
        raise KeyError(f"node id(s) not found among top-level DOCX blocks: {missing}")
    if start > end:  # type: ignore[operator]
        start, end = end, start
    return start, end  # type: ignore[return-value]


def extract_docx_ranges(
    source: str | Path | bytes | BinaryIO,
    ranges: list[tuple[str, str]],
) -> list[bytes]:
    """Extract many block ranges from one DOCX as standalone DOCX byte strings.

    Args:
        source: a DOCX path, bytes, or binary file object.
        ranges: ``(start_node_id, end_node_id)`` pairs, each inclusive.

    Returns one DOCX per range, in input order. The trailing ``<w:sectPr>`` (page
    setup) and every other package part (styles, numbering, headers/footers,
    media, rels) are preserved.
    """
    from docx import Document

    source_bytes = _coerce_to_bytes(source)
    if not ranges:
        return []

    template_doc = Document(BytesIO(source_bytes))
    blocks = list(_iter_body_blocks(template_doc))
    node_to_index = {_paragraph_node_id(index): index for index, _el in blocks}

    outputs: list[bytes] = []
    for start_node_id, end_node_id in ranges:
        start_index, end_index = _resolve_range(node_to_index, start_node_id, end_node_id)
        final_sect_pr = _sect_pr_for_range_end(template_doc.element.body, end_index)

        doc = deepcopy(template_doc)
        _trim_body_to_range(doc.element.body, start_index, end_index)
        _replace_body_sect_pr(doc.element.body, final_sect_pr)

        buffer = BytesIO()
        doc.save(buffer)
        outputs.append(_prune_docx_media(buffer.getvalue()))
    return outputs


def extract_docx_range(
    source: str | Path | bytes | BinaryIO,
    *,
    start_node_id: str,
    end_node_id: str,
) -> bytes:
    """Extract a single contiguous block range from a DOCX document.

    Thin wrapper over :func:`extract_docx_ranges` for the one-range case.
    """
    return extract_docx_ranges(source, [(start_node_id, end_node_id)])[0]


__all__ = ["extract_docx_range", "extract_docx_ranges"]
