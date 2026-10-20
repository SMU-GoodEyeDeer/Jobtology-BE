"""Resume quotations must retain ODL's source text and physical page metadata."""
from jobtology_be.document_processor import DocumentInput, read_document
from jobtology_be.document_processor.pdf.odl import build_doc_ir_from_odl_result


def test_incomplete_pdf_spans_do_not_erase_punctuation_from_source_quote():
    document = build_doc_ir_from_odl_result({
        'kids': [{'type': 'paragraph', 'content': 'Hello, world', 'page number': 1,
                  'spans': [{'content': 'Hello'}, {'content': 'world'}]}],
    })
    result = read_document(document=DocumentInput(doc_ir=document), include_runs=False)
    assert result.paragraphs[0].text == 'Hello, world'
    assert result.paragraphs[0].page_number == 1


def test_pdf_layouts_alone_preserve_page_numbers_and_dimensions():
    document = build_doc_ir_from_odl_result({
        'pages': [{'page number': 2, 'width pt': 612, 'height pt': 792}], 'kids': [],
    })
    assert [page.page_number for page in document.pages] == [2]
    assert document.pages[0].width_pt == 612
    assert document.pages[0].height_pt == 792
