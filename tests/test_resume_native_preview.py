import hashlib
from html.parser import HTMLParser

from jobtology_be.document_processor import DocIR, DocumentInput, TextAnnotation, render_review_html
from jobtology_be.document_processor.annotations import REVIEW_CSS
from jobtology_be.document_processor.models import (
    PageInfo,
    ParagraphIR,
    RunIR,
    TableCellIR,
    TableIR,
)
from jobtology_be.document_processor.style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo
from jobtology_be.resume.models import ResumeBlock
from jobtology_be.resume.preview import safe_preview, source_annotations


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_native_pages_and_styles_survive_inert_preview():
    paragraph = ParagraphIR(content=[RunIR(text='Python API', page_number=2,
        run_style=RunStyleInfo(size_pt=18, color='#123456', bold=True))],
        para_style=ParaStyleInfo(left_indent_pt=20))
    paragraph.recompute_text()
    doc = DocIR(paragraphs=[paragraph], pages=[PageInfo(page_number=2, width_pt=595, height_pt=842)])
    block = ResumeBlock(block_id='a' * 24, text=paragraph.text, text_hash=hashlib.sha256(paragraph.text.encode()).hexdigest(), source_node_id=paragraph.node_id)
    annotations = source_annotations(doc, [block])
    assert len(annotations) == 1 and isinstance(annotations[0], TextAnnotation)
    rendered = render_review_html(document=DocumentInput(doc_ir=doc), annotations=annotations)
    assert rendered.ok
    html = safe_preview(rendered.html)
    assert REVIEW_CSS in html
    elements = Elements(html).elements
    page = next(attrs for _, attrs in elements if attrs.get('class') == 'document-page')
    assert page['data-page-number'] == '2'
    assert 'width:595.0pt' in page['style'] and 'min-height:842.0pt' in page['style']
    assert 'document-page__content' in html and 'padding:48.0pt 42.0pt' in html
    assert 'font-size:18.0pt' in html and 'color:#123456' in html and '<b>' in html
    assert 'padding-left:20.0pt' in html and 'data-label="resume-block:' in html


def test_native_annotation_anchors_repeated_split_text_inside_table_cells():
    text = '😀 Python Python'
    paragraph = ParagraphIR(content=[RunIR(text='😀 Python Py'), RunIR(text='thon')])
    paragraph.recompute_text()
    cell = TableCellIR(paragraphs=[paragraph], cell_style=CellStyleInfo(width_pt=210, padding_left_pt=6, background='#abc'))
    doc = DocIR(paragraphs=[ParagraphIR(content=[TableIR(cells=[[cell]])])])
    block = ResumeBlock(block_id='b' * 24, text='Python', text_hash=hashlib.sha256(b'Python').hexdigest(), source_node_id=paragraph.node_id, source_start=9)
    rendered = render_review_html(document=DocumentInput(doc_ir=doc), annotations=source_annotations(doc, [block]))
    assert rendered.ok
    html = safe_preview(rendered.html)
    assert html.count('data-label="resume-block:') == 2
    assert 'width:210.0pt' in html and 'background-color:#abc' in html
    assert '😀 Python ' in html and text == paragraph.text


def test_layout_allowlist_rejects_css_networks_injections_and_arbitrary_stylesheets():
    html = safe_preview('''<head><style>@import "https://private";body{display:none}</style></head>
    <section class="document-page evil" style="width:595pt;position:fixed;background:url(https://private)">
    <p style="font-size:14pt;color:#123;behavior:expression(alert(1));padding:8pt;--bad:url(x);font-family:evil;background-image:u\\72l(x)" onclick="bad()">text</p>
    <iframe srcdoc="bad"></iframe><script>bad()</script><img src="https://private"></section>''')
    assert 'https://private' not in html and 'position:fixed' not in html
    assert 'expression(' not in html and 'font-family:evil' not in html and 'background-image' not in html
    assert 'onclick' not in html and '<script' not in html and '<iframe' not in html and '<img' not in html
    assert 'class="document-page"' in html and 'width:595pt' in html and 'font-size:14pt' in html
    assert 'body{display:none}' not in html
