"""Document-level render preparation before HTML export."""

from __future__ import annotations

from .models import DocIR, ParagraphContentNode, ParagraphIR, paragraph_page_number


def prepare_doc_ir_for_html(doc_ir: DocIR) -> DocIR:
    # Format-specific enrichment happens before a DocIR reaches the shared
    # renderer. This hook stays format-agnostic so `to_html()` is just rendering.
    return doc_ir


def _node_page_number(node: ParagraphContentNode) -> int | None:
    return getattr(node, "page_number", None)


def paragraph_page_fragments(paragraph: ParagraphIR) -> list[ParagraphIR]:
    """Split a paragraph into page-specific render fragments when children disagree.

    Child-level page numbers are treated as split hints only when a paragraph
    contains content on more than one page.
    """

    if not paragraph.content:
        return [paragraph]

    child_pages = [_node_page_number(node) for node in paragraph.content]
    distinct_child_pages = {page for page in child_pages if page is not None}
    if len(distinct_child_pages) <= 1:
        return [paragraph]

    fragments: list[ParagraphIR] = []
    current_page: int | None = None
    current_content: list[ParagraphContentNode] = []

    def flush() -> None:
        nonlocal current_content
        if not current_content:
            return
        fragment = paragraph.model_copy(
            deep=False,
            update={"content": list(current_content)},
        )
        fragment.recompute_text()
        fragments.append(fragment)
        current_content = []

    for node, child_page in zip(paragraph.content, child_pages, strict=True):
        resolved_page = (
            child_page if child_page is not None else paragraph_page_number(paragraph)
        )
        if current_content and resolved_page != current_page:
            flush()
        current_page = resolved_page
        current_content.append(node)

    flush()
    return fragments or [paragraph]


def iter_page_aware_paragraphs(paragraphs: list[ParagraphIR]):
    for paragraph in paragraphs:
        yield from paragraph_page_fragments(paragraph)


__all__ = [
    "iter_page_aware_paragraphs",
    "paragraph_page_fragments",
    "prepare_doc_ir_for_html",
]
