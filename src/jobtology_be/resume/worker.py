"""One bounded child process per upload. Only inert, reduced preview HTML leaves it."""
import hashlib
import json
import os
import re
import resource
import signal
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from jobtology_be.resume.models import (
    MAX_BLOCK_TEXT,
    MAX_BLOCKS,
    MAX_PAGES,
    MAX_TEXT,
    ParsedResume,
    ResumeBlock,
)


class DocumentRejected(Exception):
    pass


def _paragraphs(paragraphs):
    for paragraph in paragraphs:
        yield paragraph
        for table in paragraph.tables:
            for cell in table.iter_cells():
                yield from _paragraphs(cell.paragraphs)


def preflight(path: Path) -> int | None:
    suffix = path.suffix
    magic = path.read_bytes()[:8]
    if suffix == '.pdf':
        if not magic.startswith(b'%PDF-'):
            raise DocumentRejected('FORMAT_MISMATCH')
        from pypdf import PdfReader
        pdf = PdfReader(path)
        if pdf.is_encrypted:
            raise DocumentRejected('ENCRYPTED_DOCUMENT')
        if len(pdf.pages) > MAX_PAGES:
            raise DocumentRejected('PAGE_LIMIT')
        return len(pdf.pages)
    elif suffix in {'.doc', '.hwp'}:
        if magic != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
            raise DocumentRejected('FORMAT_MISMATCH')
    else:
        if not zipfile.is_zipfile(path):
            raise DocumentRejected('FORMAT_MISMATCH')
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(item.file_size for item in entries) > 40 * 1024 * 1024:
                raise DocumentRejected('ARCHIVE_LIMIT')
            names = {item.filename for item in entries}
            if suffix == '.docx' and 'word/document.xml' not in names:
                raise DocumentRejected('FORMAT_MISMATCH')
            if suffix == '.hwpx' and not any(re.fullmatch(r'Contents/section\d+\.xml', name) for name in names):
                raise DocumentRejected('FORMAT_MISMATCH')
            for item in entries:
                name = PurePosixPath(item.filename)
                if (name.is_absolute() or '..' in name.parts or '\\' in item.filename
                        or item.flag_bits & 1 or item.file_size > 15 * 1024 * 1024
                        or item.file_size / max(item.compress_size, 1) > 200
                        or 'vbaproject' in item.filename.lower()):
                    raise DocumentRejected('ARCHIVE_LIMIT')
                if item.filename.lower().endswith(('.xml', '.rels')):
                    xml = archive.read(item)
                    # Parsers must not expand document-supplied DTDs/entities.
                    normalized = xml.replace(b'\x00', b'').upper()
                    if b'<!DOCTYPE' in normalized or b'<!ENTITY' in normalized:
                        raise DocumentRejected('UNSAFE_XML')


def visible_docx(path: Path) -> tuple[Path, bool]:
    """Resolve visibility per run, including inherited styles and explicit overrides."""
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    removed = False

    def properties(node):
        if node is None:
            return {}
        return {name: child.get(f'{w}val', 'true').lower() not in {'0', 'false', 'off'}
                for name in ('vanish', 'webHidden')
                if (child := node.find(f'{w}{name}')) is not None}

    with zipfile.ZipFile(path) as source:
        styles_by_id = {}
        default_styles = {}
        defaults = {}
        if 'word/styles.xml' in source.namelist():
            styles = ET.fromstring(source.read('word/styles.xml'))
            defaults = properties(styles.find(f'{w}docDefaults/{w}rPrDefault/{w}rPr'))
            for style in styles.findall(f'{w}style'):
                identity = style.get(f'{w}styleId')
                parent = style.find(f'{w}basedOn')
                styles_by_id[identity] = (
                    parent.get(f'{w}val') if parent is not None else None,
                    properties(style.find(f'{w}rPr')),
                )
                if style.get(f'{w}default', '').lower() in {'1', 'true', 'on'}:
                    default_styles[style.get(f'{w}type')] = identity

        def style_properties(identity, seen=frozenset()):
            if identity is None or identity in seen or identity not in styles_by_id:
                return {}
            parent, own = styles_by_id[identity]
            return style_properties(parent, seen | {identity}) | own

        def selected_style(node, style_type):
            style = node.find(f'{w}pPr/{w}pStyle' if style_type == 'paragraph'
                              else f'{w}rPr/{w}rStyle')
            identity = style.get(f'{w}val') if style is not None else default_styles.get(style_type)
            return style_properties(identity)

        def remove_hidden(parent, paragraph_properties):
            nonlocal removed
            for child in list(parent):
                if child.tag in {f'{w}del', f'{w}moveFrom'}:
                    parent.remove(child)
                    removed = True
                    continue
                inherited = paragraph_properties
                if child.tag == f'{w}p':
                    inherited = defaults | selected_style(child, 'paragraph')
                if child.tag == f'{w}r':
                    effective = (inherited | selected_style(child, 'character')
                                 | properties(child.find(f'{w}rPr')))
                    if any(effective.values()):
                        parent.remove(child)
                        removed = True
                        continue
                remove_hidden(child, inherited)

        destination = path.with_name('visible.docx')
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == 'word/document.xml':
                    root = ET.fromstring(data)
                    remove_hidden(root, defaults)
                    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                target.writestr(item, data)
    return destination, removed


def extract(path: Path) -> ParsedResume:
    is_pdf = path.suffix == '.pdf'
    page_count = preflight(path)
    warnings = ['EXTRACTED_TEXT_REVIEW', 'KEYWORDS_ARE_NOT_PROOF']
    if path.suffix == '.docx':
        path, hidden = visible_docx(path)
        if hidden:
            warnings.append('HIDDEN_TEXT_REMOVED')
    from jobtology_be.document_processor import DocIR, DocumentInput, read_document
    doc = DocIR.from_file(path, config={'image_output': 'off'})
    if len(doc.pages) > MAX_PAGES:
        raise DocumentRejected('PAGE_LIMIT')
    skipped = tuple(page.page_number for page in doc.pages if page.parse_skip_reason)
    if skipped:
        warnings.append('OCR_REQUIRED')
    blocks = []
    count = 0
    start = 0
    truncated = False
    while True:
        result = read_document(document=DocumentInput(doc_ir=doc), start=start, limit=100,
                               include_runs=False)
        for paragraph in result.paragraphs:
            # Table-container text repeats its child paragraphs; keep the child anchors.
            text = paragraph.text
            if paragraph.has_tables:
                # Preserve text beside a nested table, without repeating cell text.
                direct = next((p for p in _paragraphs(doc.paragraphs)
                               if p.node_id == paragraph.node_id), None)
                text = ''.join(run.text for run in direct.runs) if direct else ''
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            if not text.strip():
                continue
            for offset in range(0, len(text), MAX_BLOCK_TEXT):
                fragment = text[offset:offset + MAX_BLOCK_TEXT]
                if count + len(fragment) > MAX_TEXT or len(blocks) >= MAX_BLOCKS:
                    truncated = True
                    break
                identity = f'{paragraph.node_id}:{offset}'
                blocks.append(ResumeBlock(
                    block_id=hashlib.sha256(identity.encode()).hexdigest()[:24], text=fragment,
                    text_hash=hashlib.sha256(fragment.encode()).hexdigest(),
                    page=paragraph.page_number if is_pdf and paragraph.page_number and paragraph.page_number <= MAX_PAGES else None,
                    source_node_id=paragraph.node_id, source_start=offset,
                ))
                count += len(fragment)
            if truncated:
                break
        if truncated or result.next_start is None:
            break
        start = result.next_start
    if truncated:
        warnings.append('TEXT_TRUNCATED')
    if not blocks:
        warnings.append('NO_READABLE_TEXT')
    from jobtology_be.document_processor import render_review_html
    from jobtology_be.resume.preview import safe_preview
    try:
        preview = render_review_html(document=DocumentInput(doc_ir=doc), annotations=[], title='이력서')
        preview_html = safe_preview(preview.html) if preview.ok and preview.html else None
    except Exception:  # noqa: BLE001 -- optional rendering must not discard readable text.
        preview_html = None
    if preview_html is None:
        warnings.append('PREVIEW_UNAVAILABLE')
    return ParsedResume(
        blocks=tuple(blocks), warnings=tuple(warnings), skipped_pages=skipped,
        page_count=page_count,
        preview_html=preview_html,
    )


def main() -> None:
    # The parent also kills the entire process group at the wall-clock deadline.
    resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    # An independent deadline also covers a web worker crashing during parsing.
    signal.signal(signal.SIGALRM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    signal.alarm(60)
    path, output = map(Path, sys.argv[1:3])
    try:
        from jobtology_be.resume.isolation import restrict_parser_network
        restrict_parser_network()
    except Exception as error:  # noqa: BLE001 -- fail closed before opening the document.
        output.write_text(json.dumps({
            'ok': False, 'code': 'PARSER_ISOLATION_UNAVAILABLE', 'error_type': type(error).__name__,
        }))
        return
    try:
        result = {'ok': True, 'result': extract(path).model_dump(mode='json')}
    except DocumentRejected as error:
        result = {'ok': False, 'code': str(error)}
    except Exception as error:  # noqa: BLE001 -- isolate arbitrary third-party parser failures.
        # No parser traceback, filename or résumé content in the API/logs.
        result = {'ok': False, 'code': 'UNREADABLE_DOCUMENT', 'error_type': type(error).__name__}
    output.write_text(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
