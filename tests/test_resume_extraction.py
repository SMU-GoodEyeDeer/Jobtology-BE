import asyncio
import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from test_auth_session_api import FRONTEND_ORIGIN, RecordingSessionStore, _session_cookie
from test_capabilities_api import RecordingCapabilityService, StaticIdentityProvider
from test_resume_scanner import USER, application, block
from test_specs import NCS, catalog

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.application.specs import SpecDetails, spec_credit
from jobtology_be.llm.client import LlmUnavailableError
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session import issue_session_credentials
from jobtology_be.resume.extraction import extract_paragraph, validated_entries
from jobtology_be.resume.models import ParsedResume, ResumeBlock, ResumeScan
from jobtology_be.resume.preview import safe_preview
from jobtology_be.resume.service import ResumeService
from jobtology_be.settings import Settings


def entry(text, **changes):
    return {'title': '로그인 구현 프로젝트', 'category': 'project', 'quote': text,
            'occurrence_index': 0, 'competency_ids': [NCS], 'fields': [], **changes}


def document(blocks):
    return ResumeScan(blocks=blocks, warnings=(), skipped_pages=(), page_count=3,
                      document_sha256='a' * 64, suggestions=())


def test_extraction_preserves_multiple_entries_and_fields_without_keyword_matches():
    text = '😀 가상회사에서 로그인 흐름을 구현했습니다. 서울대학교 컴퓨터공학 학사 2023.02 졸업.'
    project = entry('가상회사에서 로그인 흐름을 구현했습니다.', fields=[
        {'name': 'organization', 'value': '가상회사'}, {'name': 'role', 'value': 'invented role'},
    ])
    education = entry('서울대학교 컴퓨터공학 학사 2023.02 졸업.', category='education', title='학력',
                      competency_ids=[], fields=[{'name': 'school', 'value': '서울대학교'},
                                                 {'name': 'ended_on', 'value': '2023.02'}])
    result = validated_entries([project, education, project, entry('invented quote')], block(text), catalog().items())
    assert len(result.suggestions) == 2
    assert result.rejected_count == 3  # Two invented fields (including the duplicate) and one quote.
    assert result.suggestions[0].start == 2
    assert result.suggestions[0].fields == {'organization': '가상회사'}
    assert result.suggestions[1].fields['ended_on'] == '2023.02'
    assert not result.suggestions[1].competencies
    assert validated_entries([project], block(text), catalog().items()).suggestions[0].suggestion_id == result.suggestions[0].suggestion_id


def test_extraction_rejects_unknown_ids_and_preserves_repeated_unicode_quote_identity():
    value = entry('Python', occurrence_index=1)
    result = validated_entries([value, {**value, 'competency_ids': ['invented']},
                                {**value, 'occurrence_index': True}], block('😀 Python Python'), catalog().items())
    assert len(result.suggestions) == 1
    assert result.suggestions[0].start == 9
    assert result.rejected_count == 2


def test_one_call_per_paragraph_omits_contacts_and_bounds_prompt_catalog():
    class Llm:
        def __init__(self):
            self.calls = []
        async def complete_json(self, **kwargs):
            self.calls.append(kwargs)
            return {'entries': []}
    llm = Llm()
    text = 'user@example.com 010-1234-5678 로그인 흐름을 구현했습니다.'
    result = asyncio.run(extract_paragraph(llm, block(text), catalog().items(), 'user@example.com'))
    assert not result.error_code and not result.suggestions
    assert len(llm.calls) == 1
    sent = llm.calls[0]['messages'][0].text
    assert 'user@example.com' not in sent and '010-1234-5678' not in sent
    assert json.loads(sent)['current_paragraph'].endswith('로그인 흐름을 구현했습니다.')
    assert llm.calls[0]['schema']['properties']['entries']['maxItems'] == 5


def test_parallel_extraction_streams_out_of_order_with_a_hard_concurrency_bound(tmp_path):
    async def exercise():
        class Llm:
            active = maximum = calls = 0
            async def complete_json(self, **kwargs):
                self.calls += 1; self.active += 1; self.maximum = max(self.maximum, self.active)
                try:
                    text = json.loads(kwargs['messages'][0].text)['current_paragraph']
                    await asyncio.sleep(.06 if text.endswith('0') else .01)
                    if text.endswith('1'):
                        raise LlmUnavailableError('synthetic private failure')
                    return {'entries': [entry(text)]}
                finally:
                    self.active -= 1
        llm = Llm()
        blocks = tuple(ResumeBlock(block_id=f'{index:024x}', text=f'프로젝트 {index}',
                                   text_hash=hashlib.sha256(f'프로젝트 {index}'.encode()).hexdigest()) for index in range(9))
        service = ResumeService(catalog(), llm=llm, root=tmp_path)
        events = [event async for event in service.analyze_events(USER, document(blocks), None)]
        paragraphs = [event for event in events if event['type'] == 'paragraph']
        assert len(paragraphs) == llm.calls == 9
        assert llm.maximum == 4 and llm.active == 0
        assert paragraphs[0]['block_id'] != blocks[0].block_id
        assert [event['completed'] for event in paragraphs] == list(range(1, 10))
        assert sum(len(event['suggestions']) for event in paragraphs) == 8
        assert events[-1]['type'] == 'done' and events[-1]['failed'] == 1
        with service.slot(USER):
            pass
    asyncio.run(exercise())


def test_cancelling_stream_stops_inflight_calls_and_releases_account_slot(tmp_path):
    async def exercise():
        started = asyncio.Event()
        class Llm:
            active = 0
            async def complete_json(self, **kwargs):
                self.active += 1; started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.active -= 1
        llm = Llm()
        service = ResumeService(catalog(), llm=llm, root=tmp_path)
        stream = service.analyze_events(USER, document((block(),)), None)
        assert (await anext(stream))['type'] == 'scan'
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(started.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await stream.aclose()
        assert llm.active == 0
        with service.slot(USER):
            pass
    asyncio.run(exercise())


def test_ai_import_requires_consent_pdf_and_three_pages_before_any_model_call(tmp_path):
    class Llm:
        calls = 0
        async def complete_json(self, **kwargs):
            self.calls += 1
            return {'entries': []}
    llm = Llm()
    app, _ = application(tmp_path, llm=llm)
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=600, height=800)
    data = io.BytesIO(); writer.write(data)
    with TestClient(app) as client:
        assert client.post('/api/v1/me/resume/analyze?format=pdf', content=b'private').json()['error']['code'] == 'AI_CONSENT_REQUIRED'
        assert client.post('/api/v1/me/resume/analyze?format=docx&ai_consent=true', content=b'private').json()['error']['code'] == 'AI_PDF_REQUIRED'
        response = client.post('/api/v1/me/resume/analyze?format=pdf&ai_consent=true', content=data.getvalue())
    assert response.status_code == 422 and response.json()['error']['code'] == 'PAGE_LIMIT'
    assert llm.calls == 0


def test_ai_import_rejects_unauthenticated_and_csrf_requests_before_model_or_parser(tmp_path, monkeypatch):
    credentials = issue_session_credentials()
    app, service = application(tmp_path, session=RecordingSessionStore(credentials), llm=object())
    async def unexpected(*args, **kwargs):
        pytest.fail('unauthorized request reached parser')
    monkeypatch.setattr(service, 'scan', unexpected)
    url = '/api/v1/me/resume/analyze?format=pdf&ai_consent=true'
    with TestClient(app) as client:
        assert client.post(url, content=b'private').status_code == 401
        assert client.post(url, content=b'private', cookies=_session_cookie(credentials),
                           headers={'Origin': FRONTEND_ORIGIN}).status_code == 403


def test_optional_preview_failure_preserves_readable_text(tmp_path, monkeypatch):
    from test_resume_scanner import docx_bytes

    from jobtology_be import document_processor
    from jobtology_be.resume.worker import extract
    source = tmp_path / 'input.docx'
    source.write_bytes(docx_bytes())
    def broken_preview(**kwargs):
        raise ValueError('synthetic renderer failure')
    monkeypatch.setattr(document_processor, 'render_review_html', broken_preview)
    result = extract(source)
    assert result.blocks and result.preview_html is None
    assert 'PREVIEW_UNAVAILABLE' in result.warnings


def test_paragraph_timeout_does_not_retry_or_discard_other_results(monkeypatch):
    from jobtology_be.resume import extraction
    monkeypatch.setattr(extraction, 'AI_PARAGRAPH_SECONDS', .01)
    class Llm:
        calls = 0
        async def complete_json(self, **kwargs):
            self.calls += 1
            await asyncio.sleep(1)
    llm = Llm()
    result = asyncio.run(extract_paragraph(llm, block(), catalog().items(), ''))
    assert result.error_code == 'AI_TIMEOUT' and llm.calls == 1


def test_analysis_endpoint_streams_validated_fields_without_saving_or_retaining_upload(tmp_path, monkeypatch):
    text = '가상회사에서 2024.01.02 로그인 API 프로젝트를 구현했습니다.'
    class Llm:
        async def complete_json(self, **kwargs):
            return {'entries': [entry(text, fields=[{'name': 'organization', 'value': '가상회사'}])]}
    app, service = application(tmp_path, llm=Llm())
    async def parse(*args):
        return ParsedResume(blocks=(block(text),), page_count=3, warnings=(), preview_html='<p>preview</p>')
    monkeypatch.setattr(service, '_parse', parse)
    with TestClient(app) as client:
        response = client.post('/api/v1/me/resume/analyze?format=pdf&ai_consent=true', content=b'synthetic')
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.headers['content-type'].startswith('application/x-ndjson')
    assert response.headers['cache-control'] == 'no-store'
    assert [event['type'] for event in events] == ['scan', 'paragraph', 'done']
    assert events[1]['suggestions'][0]['fields'] == {'organization': '가상회사'}
    assert not list(service.root.glob('scan-*'))


def test_dense_three_page_documents_are_rejected_before_model_calls(tmp_path):
    service = ResumeService(catalog(), llm=object(), root=tmp_path)
    blocks = tuple(block('x' * 1800).model_copy(update={'block_id': f'{index:024x}'}) for index in range(8))
    from jobtology_be.resume.service import ResumeError
    with pytest.raises(ResumeError, match='AI_DOCUMENT_LIMIT'):
        service.validate_analysis(document(blocks))


def test_preview_drops_active_html_network_styles_and_preserves_anchor_tables():
    result = safe_preview('''<html><head><style>body{background:url(https://private)}</style></head><body>
    <script>alert(1)</script><iframe src="https://private"></iframe><img src="https://private">
    <table><tr><td colspan="2" onclick="alert(1)"><p data-node-id="p.1" style="text-align:center;background:url(https://private)">x &amp; y</p></td></tr></table></body></html>''')
    assert 'script' not in result and 'iframe' not in result and 'onclick' not in result
    assert 'https://private' not in result and '<img' not in result
    assert 'data-node-id="p.1"' in result and 'colspan="2"' in result
    assert 'x &amp; y' in result and 'text-align:center' in result


@pytest.mark.parametrize('category', ['education', 'other'])
def test_information_records_never_create_competency_credit(category):
    details = SpecDetails(competency_ids=(NCS,), evidence='실제 수행 내용이 길게 적혀 있어도 학력과 기타 기록은 역량 점수로 쓰지 않습니다.')
    from datetime import date
    assert spec_credit(details, 'PRODUCTION', category, date(2026, 10, 21)) == 0


@pytest.mark.parametrize('category', ['education', 'other'])
def test_information_api_accepts_no_proficiency_but_rejects_competency_claims(category):
    service = RecordingCapabilityService()
    app = create_app(Settings(_env_file=None, environment='test'), dependencies=ApiDependencies(
        identity_provider=StaticIdentityProvider(), capability_service=service,
    ))
    payload = {'expected_profile_version': 3, 'category': category, 'raw_text': '학력·기타 기록',
               'details': {'source': 'MANUAL', 'school': '학교'}}
    with TestClient(app) as client:
        assert client.post('/api/v1/me/capabilities', json=payload).status_code == 201
        assert service.command.proficiency is None
        assert service.command.details['school'] == '학교'
        payload['details']['competency_ids'] = [NCS]
        assert client.post('/api/v1/me/capabilities', json=payload).status_code == 422
