"""DOCX structured mapping exporter."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument


def _load_docx_source(source: "DocxDocument | str | Path | bytes"):
    from docx import Document as load_docx
    from docx.document import Document as DocxDocument

    if isinstance(source, DocxDocument):
        return source
    if isinstance(source, bytes):
        return load_docx(BytesIO(source))
    return load_docx(str(source))


def _iter_blocks(
    doc,
    *,
    CT_P,
    CT_Tbl,
    Paragraph,
    Table,
) -> Iterator[object]:
    """Yield document blocks in source order."""
    iter_inner_content = getattr(doc, "iter_inner_content", None)
    if callable(iter_inner_content):
        yield from iter_inner_content()
        return

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def _iter_blocks_from_element(
    parent,
    element,
    *,
    CT_P,
    CT_Tbl,
    Paragraph,
    Table,
) -> Iterator[object]:
    """Yield paragraph/table blocks from an arbitrary OOXML container."""
    for child in element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _export_table(
    mapping: dict[str, str],
    table,
    table_base: str,
    *,
    include_tables: bool,
    skip_empty: bool,
    CT_P,
    CT_Tbl,
    Paragraph,
    Table,
) -> None:
    for tr_idx, row in enumerate(table.rows, start=1):
        for tc_idx, cell in enumerate(row.cells, start=1):
            cp_idx = 0
            current_paragraph_base: str | None = None
            nested_table_counter_by_paragraph: dict[str, int] = {}

            for block in _iter_blocks_from_element(
                cell,
                cell._tc,
                CT_P=CT_P,
                CT_Tbl=CT_Tbl,
                Paragraph=Paragraph,
                Table=Table,
            ):
                if isinstance(block, Paragraph):
                    cp_idx += 1
                    current_paragraph_base = f"{table_base}.tr{tr_idx}.tc{tc_idx}.p{cp_idx}"

                    if not block.runs:
                        text = block.text
                        if skip_empty and not text:
                            continue
                        mapping[f"{current_paragraph_base}.r1"] = text
                        continue

                    for cr_idx, run in enumerate(block.runs, start=1):
                        text = run.text
                        if skip_empty and not text:
                            continue
                        mapping[f"{current_paragraph_base}.r{cr_idx}"] = text
                    continue

                if not include_tables or not isinstance(block, Table):
                    continue

                if current_paragraph_base is None:
                    cp_idx += 1
                    current_paragraph_base = f"{table_base}.tr{tr_idx}.tc{tc_idx}.p{cp_idx}"

                tbl_counter = nested_table_counter_by_paragraph.get(current_paragraph_base, 0) + 1
                nested_table_counter_by_paragraph[current_paragraph_base] = tbl_counter
                nested_table_base = f"{current_paragraph_base}.tbl{tbl_counter}"
                _export_table(
                    mapping,
                    block,
                    nested_table_base,
                    include_tables=include_tables,
                    skip_empty=skip_empty,
                    CT_P=CT_P,
                    CT_Tbl=CT_Tbl,
                    Paragraph=Paragraph,
                    Table=Table,
                )


def export_docx_structured_mapping(
    source: "DocxDocument | str | Path | bytes",
    *,
    include_tables: bool = True,
    skip_empty: bool = False,
) -> dict[str, str]:
    """Export DOCX text fragments keyed by HWPX-compatible structural paths."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = _load_docx_source(source)
    mapping: dict[str, str] = {}

    p_idx = 0
    tbl_counter = 0

    for block in _iter_blocks(
        doc,
        CT_P=CT_P,
        CT_Tbl=CT_Tbl,
        Paragraph=Paragraph,
        Table=Table,
    ):
        if isinstance(block, Paragraph):
            p_idx += 1
            base = f"s1.p{p_idx}"

            if not block.runs:
                text = block.text
                if skip_empty and not text:
                    continue
                mapping[f"{base}.r1"] = text
                continue

            for r_idx, run in enumerate(block.runs, start=1):
                text = run.text
                if skip_empty and not text:
                    continue
                mapping[f"{base}.r{r_idx}"] = text
            continue

        if not include_tables or not isinstance(block, Table):
            continue

        tbl_counter += 1
        p_idx += 1
        tbl_base = f"s1.p{p_idx}.r1.tbl{tbl_counter}"
        _export_table(
            mapping,
            block,
            tbl_base,
            include_tables=include_tables,
            skip_empty=skip_empty,
            CT_P=CT_P,
            CT_Tbl=CT_Tbl,
            Paragraph=Paragraph,
            Table=Table,
        )

    return mapping


def export_structured_mapping(
    source: "DocxDocument | str | Path | bytes",
    *,
    include_tables: bool = True,
    skip_empty: bool = False,
) -> dict[str, str]:
    return export_docx_structured_mapping(
        source,
        include_tables=include_tables,
        skip_empty=skip_empty,
    )


__all__ = ["export_docx_structured_mapping", "export_structured_mapping", "_iter_blocks"]
