"""Reduce processor HTML to an inert document fragment with stable source anchors."""
import re
from html import escape
from html.parser import HTMLParser

ALLOWED = frozenset({
    'div', 'section', 'article', 'p', 'span', 'br', 'hr', 'strong', 'b', 'em', 'i', 'u',
    's', 'sub', 'sup', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'colgroup', 'col', 'mark',
})
VOID = frozenset({'br', 'hr', 'col'})
DROP = frozenset({'head', 'script', 'style', 'svg', 'math', 'iframe', 'object', 'template'})


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
                safe.append(f'data-node-id="{escape(value, quote=True)}"')
            elif name in {'colspan', 'rowspan', 'span'} and value.isdigit() and 1 <= int(value) <= 100:
                safe.append(f'{name}="{int(value)}"')
            elif name == 'style':
                # Only a small layout subset. No source CSS URLs, positioning,
                # font families, escapes, active content or network resources.
                declarations = []
                for declaration in value.split(';'):
                    key, _, val = declaration.strip().partition(':')
                    key, val = key.strip().lower(), val.strip().lower()
                    if (key == 'text-align' and val in {'left', 'right', 'center', 'justify'}
                            or key == 'font-weight' and val in {'normal', 'bold', '400', '600', '700'}
                            or key == 'font-style' and val in {'normal', 'italic'}):
                        declarations.append(f'{key}:{val}')
                if declarations:
                    safe.append(f'style="{escape(";".join(declarations), quote=True)}"')
        self.output.append(f'<{tag}{(" " + " ".join(safe)) if safe else ""}>')

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
    result = ''.join(parser.output)
    return result if len(result) <= 500_000 else None
