"""Preserve native review layout while allowing only inert, typed source styles."""
import re
from html import escape
from html.parser import HTMLParser

from jobtology_be.document_processor.annotations import REVIEW_CSS
from jobtology_be.document_processor.pdf.meta import sanitize_css_color

ALLOWED = frozenset({
    'div', 'section', 'article', 'p', 'span', 'br', 'hr', 'strong', 'b', 'em', 'i', 'u',
    's', 'sub', 'sup', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'colgroup', 'col', 'mark',
})
VOID = frozenset({'br', 'hr', 'col'})
DROP = frozenset({'head', 'script', 'style', 'svg', 'math', 'iframe', 'object', 'template'})
LENGTH = r'-?(?:\d+(?:\.\d+)?|\.\d+)(?:pt|px|em|rem|%)?'
LENGTH_KEYS = frozenset({
    'width', 'height', 'min-width', 'max-width', 'min-height', 'max-height', 'font-size',
    'text-indent', 'border-radius', 'margin', 'margin-top', 'margin-right', 'margin-bottom',
    'margin-left', 'padding', 'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
    'column-gap', '-webkit-column-gap',
})
ENUMS = {
    'text-align': {'left', 'right', 'center', 'justify'},
    'vertical-align': {'top', 'middle', 'bottom', 'baseline'},
    'font-weight': {'normal', 'bold', '400', '600', '700'},
    'font-style': {'normal', 'italic'}, 'box-sizing': {'border-box'},
    'border-collapse': {'collapse'}, 'table-layout': {'fixed', 'auto'},
    'display': {'inline-block'}, 'white-space': {'nowrap', 'pre-wrap'},
    'column-fill': {'balance'}, 'break-inside': {'auto', 'avoid'},
    'text-decoration': {'underline', 'line-through', 'underline line-through'},
}


def _safe_css(css: str) -> str:
    declarations = []
    for declaration in css.split(';'):
        key, _, value = declaration.strip().partition(':')
        key, value = key.strip().lower(), value.strip().lower()
        if key in ENUMS and value in ENUMS[key]:
            pass
        elif key in {'color', 'background-color', 'background'}:
            value = sanitize_css_color(value)
            if value is None:
                continue
        elif (key in LENGTH_KEYS
              and re.fullmatch(rf'(?:{LENGTH}|auto)(?: +(?:{LENGTH}|auto)){{0,3}}', value)
              or key in {'column-count', '-webkit-column-count'}
              and re.fullmatch(r'[1-9]|1[0-2]', value)):
            pass
        elif key in {'border', 'border-top', 'border-right', 'border-bottom', 'border-left'}:
            if value != 'none' and not re.fullmatch(
                rf'{LENGTH} (?:solid|dashed|dotted|double) #[0-9a-f]{{3,8}}', value,
            ):
                continue
        elif key == 'box-shadow' and value == '0 1px 3px rgba(0,0,0,0.08)':
            pass  # Native page chrome; no document-provided functions or URLs.
        else:
            continue
        declarations.append(f'{key}:{value}')
    return ';'.join(declarations)


class _Preview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.dropped = []

    def handle_starttag(self, tag, attrs):
        if tag in DROP or self.dropped:
            if tag in DROP:
                self.dropped.append(tag)
            return
        if tag not in ALLOWED:
            return
        safe = []
        for name, value in attrs:
            if value is None:
                continue
            if name == 'data-node-id' and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', value):
                safe.append((name, value))
            elif name == 'class':
                classes = [item for item in value.split() if re.fullmatch(r'document-[a-z_-]+', item)]
                if classes:
                    safe.append((name, ' '.join(classes)))
            elif name == 'data-label' and re.fullmatch(r'resume-block:[a-f0-9]{24}', value):
                safe.append((name, value))
            elif name in {'data-page-number', 'data-column-count', 'colspan', 'rowspan', 'span'} and value.isdigit() and 1 <= int(value) <= 100:
                safe.append((name, str(int(value))))
            elif name == 'style' and (css := _safe_css(value)):
                safe.append((name, css))
        attrs_html = ''.join(f' {name}="{escape(value, quote=True)}"' for name, value in safe)
        self.output.append(f'<{tag}{attrs_html}>')

    def handle_endtag(self, tag):
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        if tag in ALLOWED and tag not in VOID:
            self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.dropped:
            data = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', data)
            self.output.append(escape(data))


def safe_preview(html: str) -> str | None:
    parser = _Preview()
    parser.feed(html)
    parser.close()
    # Reuse the renderer's own stylesheet, never a stylesheet from the source file.
    result = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
              f'<style>{REVIEW_CSS}</style></head><body>{"".join(parser.output)}</body></html>')
    return result if len(result) <= 500_000 else None


def source_annotations(doc, blocks):
    """Native run annotations also cover text inside table cells and mixed content."""
    from jobtology_be.document_processor import TextAnnotation

    def paragraphs(items):
        for paragraph in items:
            yield paragraph
            for table in paragraph.tables:
                for cell in table.iter_cells():
                    yield from paragraphs(cell.paragraphs)

    by_node = {paragraph.node_id: paragraph for paragraph in paragraphs(doc.paragraphs)}
    annotations = []
    for block in blocks:
        if len(annotations) >= 1000:
            break  # Keep pathological run fragmentation within the parser CPU budget.
        paragraph = by_node.get(block.source_node_id)
        if paragraph is None:
            continue
        raw = ''.join(run.text for run in paragraph.runs)
        start, end = block.source_start, block.source_start + len(block.text)
        if raw[start:end] != block.text:
            continue  # Never guess where normalized/omitted text was located.
        if not paragraph.tables and not paragraph.images:
            matches = [match.start() for match in re.finditer(f'(?={re.escape(block.text)})', raw)]
            annotations.append(TextAnnotation(
                target_kind='paragraph', target_id=paragraph.node_id, selected_text=block.text,
                occurrence_index=matches.index(start), label=f'resume-block:{block.block_id}',
                color='#E4DDFF',
            ))
            continue
        cursor = 0
        for run in paragraph.runs:
            if len(annotations) >= 1000:
                break
            left, right = max(0, start - cursor), min(len(run.text), end - cursor)
            cursor += len(run.text)
            if left >= right:
                continue
            quote = run.text[left:right]
            matches = [match.start() for match in re.finditer(f'(?={re.escape(quote)})', run.text)]
            annotations.append(TextAnnotation(
                target_kind='run', target_id=run.node_id, selected_text=quote,
                occurrence_index=matches.index(left), label=f'resume-block:{block.block_id}',
                color='#E4DDFF',
            ))
    return annotations
