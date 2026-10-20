"""HWPX structured mapping exporter."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET
import zipfile

if TYPE_CHECKING:
    from ..hwpx import HwpxDocument

_HP_NS = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_HP = f"{{{_HP_NS}}}"


def _emit_run(mapping: dict[str, str], key: str, text: str | None, skip_empty: bool) -> None:
    value = text or ""
    if skip_empty and not value:
        return
    mapping[key] = value


def _run_text(run_el: ET.Element) -> str:
    return "".join("".join(t.itertext()) for t in run_el.findall(f"{_HP}t"))


def _paragraph_text(paragraph_el: ET.Element) -> str:
    return "".join(_run_text(run_el) for run_el in paragraph_el.findall(f"{_HP}run"))


def _iter_section_paragraphs(section_root: ET.Element) -> list[ET.Element]:
    return section_root.findall(f"{_HP}p")


def _iter_paragraph_tables(paragraph_el: ET.Element) -> list[ET.Element]:
    return paragraph_el.findall(f"{_HP}run/{_HP}tbl")


def _iter_cell_paragraphs(cell_el: ET.Element) -> list[ET.Element]:
    direct = cell_el.findall(f"{_HP}subList/{_HP}p")
    if direct:
        return direct
    return cell_el.findall(f".//{_HP}p")


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _logical_table_cells(row_el: ET.Element) -> list[tuple[int, ET.Element]]:
    """Return logical 1-based column indices for row cells.

    HWPX may omit vertically merged placeholder cells from later rows.
    In those cases the physical `tc` order no longer matches the logical
    table column. Prefer `cellAddr.colAddr` when present.
    """
    logical_cells: list[tuple[int, ET.Element]] = []
    fallback_col = 1

    for cell_el in row_el.findall(f"{_HP}tc"):
        cell_addr = cell_el.find(f"{_HP}cellAddr")
        col_addr = _safe_int(cell_addr.get("colAddr")) if cell_addr is not None else None
        logical_col = (col_addr + 1) if col_addr is not None else fallback_col
        logical_cells.append((logical_col, cell_el))

        cell_span = cell_el.find(f"{_HP}cellSpan")
        colspan = _safe_int(cell_span.get("colSpan")) if cell_span is not None else None
        fallback_col = max(fallback_col, logical_col + max(colspan or 1, 1))

    return logical_cells


def _export_runs_for_paragraph(
    mapping: dict[str, str],
    paragraph_el: ET.Element,
    paragraph_id: str,
    *,
    skip_empty: bool,
) -> None:
    run_els = paragraph_el.findall(f"{_HP}run")
    if not run_els:
        _emit_run(mapping, f"{paragraph_id}.r1", _paragraph_text(paragraph_el), skip_empty)
        return

    for r_idx, run_el in enumerate(run_els, start=1):
        _emit_run(mapping, f"{paragraph_id}.r{r_idx}", _run_text(run_el), skip_empty)


def _export_nested_tables_for_paragraph(
    mapping: dict[str, str],
    paragraph_el: ET.Element,
    paragraph_id: str,
    *,
    skip_empty: bool,
) -> None:
    for t_idx, table_el in enumerate(_iter_paragraph_tables(paragraph_el), start=1):
        tbl_root = f"{paragraph_id}.tbl{t_idx}"
        _export_table_xml(mapping, table_el, tbl_root, skip_empty=skip_empty)


def _export_table_xml(
    mapping: dict[str, str],
    table_el: ET.Element,
    tbl_root: str,
    *,
    skip_empty: bool,
) -> None:
    for tr_idx, row_el in enumerate(table_el.findall(f"{_HP}tr"), start=1):
        for tc_idx, cell_el in _logical_table_cells(row_el):
            cell_paragraphs = _iter_cell_paragraphs(cell_el)
            if not cell_paragraphs:
                key = f"{tbl_root}.tr{tr_idx}.tc{tc_idx}.p1.r1"
                _emit_run(mapping, key, "", skip_empty)
                continue

            for cp_idx, cp_el in enumerate(cell_paragraphs, start=1):
                paragraph_id = f"{tbl_root}.tr{tr_idx}.tc{tc_idx}.p{cp_idx}"
                _export_runs_for_paragraph(
                    mapping,
                    cp_el,
                    paragraph_id,
                    skip_empty=skip_empty,
                )
                _export_nested_tables_for_paragraph(
                    mapping,
                    cp_el,
                    paragraph_id,
                    skip_empty=skip_empty,
                )


def _export_from_section_roots(section_roots: list[ET.Element], *, skip_empty: bool) -> dict[str, str]:
    mapping: dict[str, str] = {}

    for s_idx, section_root in enumerate(section_roots, start=1):
        for p_idx, para_el in enumerate(_iter_section_paragraphs(section_root), start=1):
            base = f"s{s_idx}.p{p_idx}"
            _export_runs_for_paragraph(mapping, para_el, base, skip_empty=skip_empty)

            for t_idx, table_el in enumerate(_iter_paragraph_tables(para_el), start=1):
                tbl_root = f"{base}.r1.tbl{t_idx}"
                _export_table_xml(mapping, table_el, tbl_root, skip_empty=skip_empty)

    return mapping


def _section_roots_from_bytes(source: bytes) -> list[ET.Element]:
    section_name_pattern = re.compile(r"^Contents/section\d+\.xml$")

    with zipfile.ZipFile(BytesIO(source)) as zf:
        def _section_order(name: str) -> int:
            match = re.search(r"section(\d+)\.xml$", name)
            return int(match.group(1)) if match else -1

        names = sorted(
            (name for name in zf.namelist() if section_name_pattern.match(name)),
            key=_section_order,
        )
        return [ET.fromstring(zf.read(name)) for name in names]


def _section_roots_from_doc(doc: "HwpxDocument") -> list[ET.Element]:
    return [section.element for section in doc.sections]


def export_hwpx_structured_mapping(
    source: "HwpxDocument | str | Path | bytes",
    *,
    skip_empty: bool = False,
) -> dict[str, str]:
    """Export HWPX text fragments keyed by structural paths."""
    from ..hwpx import HwpxDocument

    if isinstance(source, HwpxDocument):
        return _export_from_section_roots(
            _section_roots_from_doc(source),
            skip_empty=skip_empty,
        )

    if isinstance(source, bytes):
        return _export_from_section_roots(
            _section_roots_from_bytes(source),
            skip_empty=skip_empty,
        )

    if isinstance(source, (str, Path)):
        with HwpxDocument.open(str(source)) as doc:
            return _export_from_section_roots(
                _section_roots_from_doc(doc),
                skip_empty=skip_empty,
            )

    raise TypeError(
        "source must be HwpxDocument, bytes, or a .hwpx path, "
        f"got {type(source)!r}"
    )


def export_structured_mapping(
    source: "HwpxDocument | str | Path | bytes",
    *,
    skip_empty: bool = False,
) -> dict[str, str]:
    return export_hwpx_structured_mapping(source, skip_empty=skip_empty)


__all__ = ["export_hwpx_structured_mapping", "export_structured_mapping"]
