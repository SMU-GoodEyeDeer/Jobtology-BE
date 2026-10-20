"""Document styles must stay inside their HTML attribute, in both exporters."""
from html.parser import HTMLParser

import pytest

from jobtology_be.document_processor import DocIR, DocumentInput, TextAnnotation, render_review_html
from jobtology_be.document_processor.html_exporter import _html_attrs
from jobtology_be.document_processor.models import ParagraphIR, RunIR, TableCellIR, TableIR
from jobtology_be.document_processor.style_types import CellStyleInfo, ParaStyleInfo, RunStyleInfo

PAYLOAD = 'red" onmouseover="probe'


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def output(doc, review):
    if not review:
        return doc.to_html()
    result = render_review_html(document=DocumentInput(doc_ir=doc), annotations=[])
    assert result.ok
    return result.html


@pytest.mark.parametrize('review', [False, True])
def test_run_paragraph_and_cell_styles_cannot_create_event_attributes(review):
    paragraph = ParagraphIR(text='safe text', para_style=ParaStyleInfo(align=PAYLOAD), content=[RunIR(
        text='safe text', run_style=RunStyleInfo(font_family=PAYLOAD, color=PAYLOAD, highlight=PAYLOAD),
    )])
    cell = TableCellIR(paragraphs=[paragraph.model_copy(deep=True)], cell_style=CellStyleInfo(
        background=PAYLOAD, horizontal_align=PAYLOAD, border_top=f'1px solid {PAYLOAD}',
    ))
    table = TableIR(row_count=1, col_count=1, cells=[[cell]])
    doc = DocIR(paragraphs=[paragraph, ParagraphIR(content=[table])])
    tags = Tags(output(doc, review)).tags
    assert any(tag == 'td' for tag, _ in tags)
    assert not any(key.startswith('on') for _, attrs in tags for key in attrs)
    styles = [attrs['style'] for _, attrs in tags if 'style' in attrs]
    assert not any('background-color:red"' in style or 'color:red"' in style for style in styles)
    assert any('text-align:red"' in style for style in styles)  # quotes stay within the style value


@pytest.mark.parametrize('review', [False, True])
def test_valid_run_and_cell_colors_keep_rendering(review):
    paragraph = ParagraphIR(text='safe text', content=[RunIR(
        text='safe text', run_style=RunStyleInfo(color='#112233', highlight='rgba(1, 2, 3, 0.5)'),
    )])
    cell = TableCellIR(paragraphs=[paragraph], cell_style=CellStyleInfo(background='red'))
    doc = DocIR(paragraphs=[ParagraphIR(content=[TableIR(row_count=1, col_count=1, cells=[[cell]])])])
    html = output(doc, review)
    assert 'color:#112233' in html
    assert 'background-color:rgba(1, 2, 3, 0.5)' in html
    assert 'background-color:red' in html


def test_generic_style_attribute_escapes_quotes():
    tags = Tags(f'<span {_html_attrs(style=PAYLOAD)}>text</span>').tags
    assert tags == [('span', {'style': PAYLOAD})]


def test_annotation_color_cannot_add_css_declarations_or_html_attributes():
    paragraph = ParagraphIR(text='safe text', content=[RunIR(text='safe text')])
    doc = DocIR(paragraphs=[paragraph])
    result = render_review_html(document=DocumentInput(doc_ir=doc), annotations=[TextAnnotation(
        target_kind='paragraph', target_id=paragraph.node_id, selected_text='safe',
        label='source evidence',
        color='red;background-image:url(https://example.invalid/pixel)',
    )])
    assert result.ok
    marks = [attrs for tag, attrs in Tags(result.html).tags if tag == 'mark']
    assert marks
    assert 'background-color:#FFFF00' in marks[0]['style']
    assert 'example.invalid' not in result.html
