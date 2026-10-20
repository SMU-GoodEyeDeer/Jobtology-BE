"""HWPX native range extractor.

Materialize a contiguous range of top-level section paragraphs from an existing
HWPX document as a standalone HWPX byte string. The original style pool
(``Contents/header.xml``) and every other package part are preserved verbatim;
only the target section body is trimmed to the requested paragraph range.

Node identity matches the DocIR / edit-engine scheme (``s{section}.p{paragraph}``),
so callers can address a range with the same ``node_id`` values a parsed ``DocIR``
exposes.

Scope (initial slice): single-section HWPX, one contiguous paragraph range.
"""

from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from typing import BinaryIO
from xml.etree import ElementTree as ET
import zipfile

from ..edit_engine import (
    _HP,
    _EditableHwpxArchive,
    _EditableHwpxSection,
    _iter_section_paragraphs,
    _serialize_hwpx_section,
)
from ..models import _stable_node_id

_CONTENT_HPF_NAME = "Contents/content.hpf"
_CONTAINER_RDF_NAME = "META-INF/container.rdf"
_HEADER_NAME = "Contents/header.xml"
_SECTION_ENTRY_TEMPLATE = "Contents/section{index}.xml"


def _coerce_to_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    read = getattr(source, "read", None)
    if callable(read):
        return read()
    raise TypeError(f"Unsupported HWPX source type: {type(source)!r}")


def _paragraph_node_id(section_index: int, paragraph_index: int) -> str:
    """Node id for a top-level section paragraph (1-based indices)."""
    return _stable_node_id("paragraph", f"s{section_index}.p{paragraph_index}")


def _has_secpr(paragraph_el: ET.Element) -> bool:
    return paragraph_el.find(f".//{_HP}secPr") is not None


def _leading_secpr_run(first_paragraph_el: ET.Element) -> ET.Element | None:
    """The section-defining control run of a section's first paragraph, content-stripped.

    In HWPX the first paragraph of a section carries the section definition in a
    leading ``hp:run``: ``hp:secPr`` (page size, margins, page-number start, page
    borders) plus ``hp:ctrl`` (column properties, headers/footers). Grafting the
    bare ``secPr`` into a content run does not work — Hangul ignores it. Returning
    the whole control run (minus visible text/tables) lets us prepend it as the
    first run of the first kept paragraph, reproducing the native structure
    (control run + content runs in one paragraph) without adding a paragraph — an
    extra empty paragraph would land on its own page when the next paragraph has
    ``pageBreakBefore``.
    """
    for run_el in first_paragraph_el.findall(f"{_HP}run"):
        if run_el.find(f".//{_HP}secPr") is None:
            continue
        clone = deepcopy(run_el)
        for child in list(clone):
            if child.tag in (f"{_HP}t", f"{_HP}tbl"):
                clone.remove(child)
        return clone
    return None


def _trim_section_to_range(section_root: ET.Element, start_para: int, end_para: int) -> None:
    """Keep only top-level paragraphs ``start_para..end_para`` (1-based, inclusive).

    When the range does not start at the section's first paragraph, the section's
    defining control run (secPr + columns/headers) is prepended to the first kept
    paragraph so page setup, page numbers, and columns survive — without inserting
    an extra paragraph.
    """
    paragraphs = _iter_section_paragraphs(section_root)
    kept = paragraphs[start_para - 1 : end_para]

    secpr_run: ET.Element | None = None
    if start_para > 1 and kept and _has_secpr(paragraphs[0]) and not _has_secpr(kept[0]):
        secpr_run = _leading_secpr_run(paragraphs[0])

    keep_ids = {id(paragraph_el) for paragraph_el in kept}
    for paragraph_el in paragraphs:
        if id(paragraph_el) not in keep_ids:
            section_root.remove(paragraph_el)

    if secpr_run is not None:
        kept[0].insert(0, secpr_run)


_SECTION_NAME_RE = re.compile(r"^Contents/section(\d+)\.xml$")


def _section_index(section_name: str) -> int:
    match = _SECTION_NAME_RE.match(section_name)
    if match is None:
        raise ValueError(f"not a section entry name: {section_name!r}")
    return int(match.group(1))


def _renumber_section_tokens(text: str, renumber: dict[int, int]) -> str:
    """Renumber ``section{old}`` tokens to ``section{new}`` across a package descriptor.

    Two-phase (via NUL-delimited placeholders) so a mapping like ``{1: 0, 2: 1}``
    never has an intermediate ``section1`` collide with an unprocessed one. Covers
    both quoted ids (``"section1"``) and file paths (``section1.xml``).
    """
    for old, new in renumber.items():
        text = text.replace(f"section{old}.xml", f"\x00SEC{new}\x00.xml")
        text = text.replace(f'"section{old}"', f'"\x00SEC{new}\x00"')
    for new in set(renumber.values()):
        text = text.replace(f"\x00SEC{new}\x00", f"section{new}")
    return text


def _rewrite_content_hpf(
    hpf_text: str,
    *,
    section_renumber: dict[int, int],
    dropped_sections: list[int],
    drop_image_ids: set[str] = frozenset(),
) -> str:
    """Drop OPF references to removed sections/images; renumber kept sections to 0..K.

    ``Contents/content.hpf`` lists each section twice (an ``<opf:item>`` in the
    manifest and an ``<opf:itemref>`` in the spine) and each embedded image once
    (an ``<opf:item>``). Edited as text so the rest of the (heavily namespaced)
    package descriptor stays byte-identical. A manifest item left pointing at a
    dropped part corrupts the file in Hangul.
    """
    for index in dropped_sections:
        hpf_text = re.sub(rf'<opf:item\s+id="section{index}"[^>]*/>', "", hpf_text)
        hpf_text = re.sub(rf'<opf:itemref\s+idref="section{index}"[^>]*/>', "", hpf_text)

    for image_id in drop_image_ids:
        hpf_text = re.sub(rf'<opf:item\s+id="{re.escape(image_id)}"[^>]*/>', "", hpf_text)

    return _renumber_section_tokens(hpf_text, section_renumber)


def _image_items(hpf_text: str) -> dict[str, str]:
    """Map each embedded-image ``<opf:item>`` id to its ``BinData/...`` href."""
    return {
        match.group("id"): match.group("href")
        for match in re.finditer(
            r'<opf:item\s+id="(?P<id>[^"]+)"\s+href="(?P<href>BinData/[^"]+)"[^>]*/>',
            hpf_text,
        )
    }


def _referenced_image_ids(section_bytes: bytes, header_bytes: bytes, candidate_ids) -> set[str]:
    """Image ids that actually appear in the kept section body or header styles.

    Matched quote-delimited (``"image1"``) so ``image1`` never matches ``image10``.
    Scans raw bytes to catch any reference form, not just ``binaryItemIDRef``.
    """
    haystack = section_bytes + b"\x00" + header_bytes
    return {
        image_id
        for image_id in candidate_ids
        if b'"' + image_id.encode("utf-8") + b'"' in haystack
    }


def _read_optional(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return archive.read(name)
    except KeyError:
        return None


def _fix_container_rdf(
    rdf_text: str,
    *,
    section_renumber: dict[int, int],
    dropped_sections: list[int],
) -> str:
    """Drop RDF part declarations for removed sections; renumber kept ones to 0..K.

    ``META-INF/container.rdf`` declares every section file twice (a ``hasPart``
    edge plus a typed ``Description``). Stale references left here are a real
    corruption source in Hangul even though lenient parsers ignore them.
    """
    for index in dropped_sections:
        resource = re.escape(_SECTION_ENTRY_TEMPLATE.format(index=index))
        rdf_text = re.sub(
            rf'<rdf:Description rdf:about=""><[^>]*hasPart[^>]*rdf:resource="{resource}"/></rdf:Description>',
            "",
            rdf_text,
        )
        rdf_text = re.sub(
            rf'<rdf:Description rdf:about="{resource}">.*?</rdf:Description>',
            "",
            rdf_text,
            flags=re.S,
        )
    return _renumber_section_tokens(rdf_text, section_renumber)


def _rewrite_header_section_count(header_bytes: bytes, *, section_count: int) -> bytes:
    """Set ``<hh:head secCnt="...">`` to the number of sections in the output.

    A ``secCnt`` that disagrees with the actual section count corrupts the file
    in Hangul.
    """
    return re.sub(
        rb'secCnt="\d+"',
        f'secCnt="{section_count}"'.encode("ascii"),
        header_bytes,
        count=1,
    )


def _copy_entry_info(info: zipfile.ZipInfo, *, filename: str) -> zipfile.ZipInfo:
    """Clone a ``ZipInfo`` under a new name, preserving compression metadata."""
    clone = deepcopy(info)
    clone.filename = filename
    return clone


def _build_output_bytes(
    source_bytes: bytes,
    section_names: list[str],
    kept_sections: list[_EditableHwpxSection],
) -> bytes:
    """Re-zip keeping ``kept_sections`` (renumbered to section0..K) as the body.

    ``kept_sections`` is the ordered, already-trimmed set of sections the range
    covers (one for a single-section range, several when it spans section breaks).
    Every non-section part (2 MB style pool, images, settings, previews, META-INF)
    is copied verbatim; dropped section bodies and their OPF/RDF references are
    removed, ``secCnt`` is set to the kept count, and images no longer referenced
    by any kept body are pruned.
    """
    all_indices = [_section_index(name) for name in section_names]
    kept_old_indices = [_section_index(section.name) for section in kept_sections]
    section_renumber = {old: new for new, old in enumerate(kept_old_indices)}
    dropped_sections = [index for index in all_indices if index not in section_renumber]
    bodies_by_old = {
        _section_index(section.name): _serialize_hwpx_section(section)
        for section in kept_sections
    }

    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(source_bytes)) as source_archive:
        # Keep only images the kept bodies (or header styles) still reference.
        content_hpf_bytes = _read_optional(source_archive, _CONTENT_HPF_NAME)
        header_bytes = _read_optional(source_archive, _HEADER_NAME) or b""
        image_map = _image_items(content_hpf_bytes.decode("utf-8")) if content_hpf_bytes else {}
        combined_bodies = b"\x00".join(bodies_by_old.values())
        used_image_ids = _referenced_image_ids(combined_bodies, header_bytes, image_map)
        drop_image_ids = set(image_map) - used_image_ids
        drop_image_hrefs = {image_map[image_id] for image_id in drop_image_ids}

        with zipfile.ZipFile(buffer, "w") as target_archive:
            for info in source_archive.infolist():
                name = info.filename
                if name in drop_image_hrefs:
                    continue
                section_match = _SECTION_NAME_RE.match(name)
                if section_match is not None:
                    old_index = int(section_match.group(1))
                    if old_index not in section_renumber:
                        continue  # dropped section
                    new_name = _SECTION_ENTRY_TEMPLATE.format(index=section_renumber[old_index])
                    target_archive.writestr(
                        _copy_entry_info(info, filename=new_name),
                        bodies_by_old[old_index],
                    )
                    continue
                if name == _CONTENT_HPF_NAME:
                    rewritten = _rewrite_content_hpf(
                        source_archive.read(name).decode("utf-8"),
                        section_renumber=section_renumber,
                        dropped_sections=dropped_sections,
                        drop_image_ids=drop_image_ids,
                    )
                    target_archive.writestr(info, rewritten.encode("utf-8"))
                    continue
                if name == _CONTAINER_RDF_NAME:
                    fixed = _fix_container_rdf(
                        source_archive.read(name).decode("utf-8"),
                        section_renumber=section_renumber,
                        dropped_sections=dropped_sections,
                    )
                    target_archive.writestr(info, fixed.encode("utf-8"))
                    continue
                if name == _HEADER_NAME:
                    target_archive.writestr(
                        info,
                        _rewrite_header_section_count(
                            source_archive.read(name), section_count=len(kept_sections)
                        ),
                    )
                    continue
                target_archive.writestr(info, source_archive.read(name))
    return buffer.getvalue()


def _locate_paragraphs(archive: _EditableHwpxArchive) -> dict[str, tuple[int, int]]:
    """Map every top-level paragraph node id to its ``(section_pos, para_pos)`` (1-based)."""
    located: dict[str, tuple[int, int]] = {}
    for section_pos, section in enumerate(archive.section_entries, start=1):
        for para_pos, _paragraph_el in enumerate(_iter_section_paragraphs(section.root), start=1):
            located[_paragraph_node_id(section_pos, para_pos)] = (section_pos, para_pos)
    return located


def _resolve_range(
    located: dict[str, tuple[int, int]],
    start_node_id: str,
    end_node_id: str,
) -> tuple[int, int, int, int]:
    """Resolve a node-id pair to ``(start_section, start_para, end_section, end_para)``.

    The pair may span sections; it is normalized to document order.
    """
    start = located.get(start_node_id)
    end = located.get(end_node_id)
    missing = [
        node_id
        for node_id, position in ((start_node_id, start), (end_node_id, end))
        if position is None
    ]
    if missing:
        raise KeyError(f"node id(s) not found among top-level HWPX paragraphs: {missing}")

    if start > end:  # (section, para) tuple order == document order
        start, end = end, start
    start_section, start_para = start  # type: ignore[misc]
    end_section, end_para = end  # type: ignore[misc]
    return start_section, start_para, end_section, end_para


def _trimmed_section(
    section: _EditableHwpxSection,
    start_para: int,
    end_para: int,
) -> _EditableHwpxSection:
    """A trimmed copy of ``section`` without mutating the shared parsed tree."""
    trimmed_root = deepcopy(section.root)
    _trim_section_to_range(trimmed_root, start_para, end_para)
    return _EditableHwpxSection(
        name=section.name,
        root=trimmed_root,
        xml_prefix=section.xml_prefix,
        original_root_open=section.original_root_open,
        namespaces=section.namespaces,
    )


def _kept_sections_for_range(
    archive: _EditableHwpxArchive,
    start_section: int,
    start_para: int,
    end_section: int,
    end_para: int,
) -> list[_EditableHwpxSection]:
    """Trimmed sections covering the range: first tail, whole middles, last head.

    A single-section range yields one trimmed section; a spanning range yields the
    first section (from ``start_para`` on), every section in between whole, and the
    last section (up to ``end_para``). Each kept section keeps its own ``secPr``.
    """
    kept: list[_EditableHwpxSection] = []
    for section_pos in range(start_section, end_section + 1):
        section = archive.section_entries[section_pos - 1]
        paragraph_count = len(_iter_section_paragraphs(section.root))
        lo = start_para if section_pos == start_section else 1
        hi = end_para if section_pos == end_section else paragraph_count
        kept.append(_trimmed_section(section, lo, hi))
    return kept


def extract_hwpx_ranges(
    source: str | Path | bytes | BinaryIO,
    ranges: list[tuple[str, str]],
) -> list[bytes]:
    """Extract many paragraph ranges from one HWPX, parsing the source once.

    Args:
        source: an HWPX path, bytes, or binary file object.
        ranges: ``(start_node_id, end_node_id)`` pairs, each inclusive. A range may
            span section breaks.

    Returns one standalone HWPX byte string per range, in input order. Parsing the
    (potentially large) source happens once regardless of how many ranges are
    requested, which is the linear-cost path for splitting a document into many
    files.
    """
    source_bytes = _coerce_to_bytes(source)
    archive = _EditableHwpxArchive.from_bytes(source_bytes)
    located = _locate_paragraphs(archive)
    section_names = [section.name for section in archive.section_entries]

    outputs: list[bytes] = []
    for start_node_id, end_node_id in ranges:
        start_section, start_para, end_section, end_para = _resolve_range(
            located, start_node_id, end_node_id
        )
        kept_sections = _kept_sections_for_range(
            archive, start_section, start_para, end_section, end_para
        )
        outputs.append(_build_output_bytes(source_bytes, section_names, kept_sections))
    return outputs


def extract_hwpx_range(
    source: str | Path | bytes | BinaryIO,
    *,
    start_node_id: str,
    end_node_id: str,
) -> bytes:
    """Extract a single contiguous paragraph range from an HWPX document.

    Thin wrapper over :func:`extract_hwpx_ranges` for the one-range case. The kept
    paragraphs must be top-level paragraphs of the same section.
    """
    return extract_hwpx_ranges(source, [(start_node_id, end_node_id)])[0]


__all__ = ["extract_hwpx_range", "extract_hwpx_ranges"]
