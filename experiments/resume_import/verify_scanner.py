"""Installed-wheel smoke: request-only résumé scanning with real child processes.

Synthetic documents only. No HTTP sessions, database writes or model calls.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from verify_processor import make_docx, make_hwpx, make_pdf

from jobtology_be.application.specs import SkillCatalog
from jobtology_be.resume.service import ResumeService


async def verify(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    make_docx(out / 'resume.docx')
    make_hwpx(out / 'resume.hwpx')
    make_pdf(out / 'text.pdf')
    make_pdf(out / 'scan.pdf', scan=True)
    service = ResumeService(SkillCatalog(lambda: ()), root=out / 'private')
    results = {}
    for name in ('resume.docx', 'resume.hwpx', 'text.pdf', 'scan.pdf'):
        async def body(name=name):
            yield (out / name).read_bytes()
        result = await service.scan(uuid4(), name.rsplit('.', 1)[1], body())
        assert not list(service.root.glob('scan-*'))
        if name == 'scan.pdf':
            assert not result.blocks and 'OCR_REQUIRED' in result.warnings
        else:
            assert any('Python' in item.text for item in result.blocks)
        if name == 'resume.docx':
            assert not any('HIDDEN_KEYWORD' in item.text for item in result.blocks)
        results[name] = {'blocks': len(result.blocks), 'warnings': result.warnings}
    print(json.dumps({'scanner': results, 'temporary_files_removed': True}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(verify(parser.parse_args().out))
