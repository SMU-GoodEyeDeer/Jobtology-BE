from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_acceptance_m5_worker import _snapshot
from test_capabilities_api import RecordingCapabilityService, StaticIdentityProvider

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.application.queries import CapabilityView
from jobtology_be.application.specs import SkillCatalog, SpecDetails, capability_inputs, spec_credit
from jobtology_be.main import create_app
from jobtology_be.modules.profiles.normalizer import CapabilityCatalogEntry
from jobtology_be.settings import Settings

NCS = 'ncs:unit:2001020211'
TODAY = date(2026, 10, 20)
EVIDENCE = 'FastAPI와 PostgreSQL로 회원 로그인 API를 직접 구현하고 테스트했어요.'


def catalog():
    original = _snapshot()
    source = replace(original, capability_entries=(CapabilityCatalogEntry(
        entity_id=NCS, aliases=frozenset({'서버프로그램 구현', '2001020211', 'Python', 'FastAPI'}),
    ),), requirements=(replace(original.requirements[0], entity_id=NCS,
                               label='서버프로그램 구현', required_experience_codes=frozenset()),))
    return SkillCatalog(lambda: (source,))


def record(category='skill', **changes):
    return replace(CapabilityView(
        capability_id=UUID(int=1), category=category, raw_text='로그인 프로젝트', entity_id=None,
        proficiency='GUIDED', verification='SELF_REPORTED', lifecycle='ACTIVE',
        details={'competency_ids': [NCS], 'evidence': EVIDENCE}, source_completion_event_id=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    ), **changes)


def test_search_uses_english_and_korean_aliases_and_real_distinct_onet_identities():
    source = catalog()
    assert source.search('python')[0].entity_id == NCS
    assert source.search('서버')[0].entity_id == NCS
    programming = source.search('Programming')[0]
    assert programming.entity_id == 'onet:skill:2.B.3.e'
    assert programming.label == '프로그래밍'
    assert programming.requirement_keys == ()
    assert source.search('프로그래밍')[0] == programming
    assert len([item for item in source.items() if item.source == 'ONET']) == 35


@pytest.mark.parametrize(('level', 'credit'), [('FAMILIAR', .25), ('GUIDED', .5), ('INDEPENDENT', .75), ('PRODUCTION', 1)])
def test_confirmed_evidence_gets_explicit_planning_credit(level, credit):
    details = SpecDetails(competency_ids=(NCS,), evidence=EVIDENCE)
    assert spec_credit(details, level, 'skill', TODAY) == credit
    result = capability_inputs(raw_text='Python', entity_id=None, category='skill', proficiency=level,
                               details=details.model_dump(), today=TODAY)
    assert result[0].entity_id == NCS
    assert result[0].raw_text == '2001020211'
    assert result[0].credit == credit


def test_bare_tags_unmapped_scores_and_onet_names_do_not_get_broad_ncs_credit():
    for details in ({}, {'competency_ids': [NCS], 'evidence': 'Python'},
                    {'competency_ids': ['onet:skill:2.B.3.e'], 'evidence': EVIDENCE}):
        values = capability_inputs(raw_text='Python', entity_id=NCS, category='skill',
                                   proficiency='PRODUCTION', details=details, today=TODAY)
        assert all(value.credit == 0 for value in values)
    assert spec_credit(SpecDetails(score=990, score_max=990), 'PRODUCTION', 'test', TODAY) == 0


@pytest.mark.parametrize('category', ['certification', 'test'])
def test_expired_and_future_certifications_are_not_current_evidence(category):
    details = SpecDetails(competency_ids=(NCS,), evidence=EVIDENCE, issuer='발급 기관',
                          expires_on=date(2026, 10, 19))
    assert spec_credit(details, 'PRODUCTION', category, TODAY) == 0
    future = details.model_copy(update={'expires_on': None, 'issued_on': date(2026, 10, 21)})
    assert spec_credit(future, 'PRODUCTION', category, TODAY) == 0
    matched = catalog().matching(record(category, details=details.model_dump()), 'BACKEND_DEVELOPER', TODAY)
    assert matched.status == 'EXPIRED'
    assert matched.credit == 0


def test_feedback_distinguishes_recognition_current_goal_and_missing_evidence():
    source = catalog()
    assert source.matching(record(), 'BACKEND_DEVELOPER').status == 'MATCHED'
    assert source.matching(record(), None).status == 'UNUSED'
    assert source.matching(record(), 'OTHER').credit == 0
    assert source.matching(record(details={}), 'BACKEND_DEVELOPER').status == 'NEEDS_CLARIFICATION'
    onet = record(details={'competency_ids': ['onet:skill:2.B.3.e'], 'evidence': EVIDENCE})
    feedback = source.matching(onet, 'BACKEND_DEVELOPER')
    assert feedback.status == 'UNUSED'
    assert feedback.competencies[0].original_label == 'Programming'


@pytest.mark.parametrize('details', [
    {'score': True}, {'score': float('inf')}, {'score': 101, 'score_max': 100},
    {'started_on': '2026-10-20', 'ended_on': '2026-01-01'},
    {'ongoing': True, 'ended_on': '2026-01-01'},
    {'issued_on': '2026-10-20', 'expires_on': '2026-01-01'},
    {'url': 'javascript:alert(1)'}, {'competency_ids': [NCS, NCS]}, {'credit': 1},
])
def test_invalid_dates_scores_links_and_client_supplied_credit_are_rejected(details):
    with pytest.raises(ValidationError):
        SpecDetails.model_validate(details)


def test_structured_api_validates_canonical_ids_and_never_calls_llm_for_search_or_save():
    service = RecordingCapabilityService()
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(
        identity_provider=StaticIdentityProvider(), capability_service=service, skill_catalog=catalog(),
    ))
    payload = {'expected_profile_version': 3, 'category': 'project', 'raw_text': '로그인 프로젝트',
               'proficiency': 'GUIDED', 'details': {'competency_ids': [NCS], 'evidence': EVIDENCE}}
    with TestClient(app) as client:
        assert client.get('/api/v1/skills?q=Python').json()['items'][0]['entity_id'] == NCS
        assert client.post('/api/v1/me/capabilities', json=payload).status_code == 201
        changed = {**payload, 'category': 'experience', 'raw_text': '서버 개발 인턴'}
        assert client.patch('/api/v1/me/capabilities/00000000-0000-0000-0000-000000000001', json=changed).status_code == 200
        assert service.command.category == 'experience'
        assert client.post('/api/v1/me/capabilities', json={**payload, 'details': {'competency_ids': ['ncs:unit:fake']}}).status_code == 422
        assert client.post('/api/v1/me/capabilities', json={**payload, 'category': 'test'}).status_code == 422
        assert client.post('/api/v1/me/capabilities', json={**payload, 'category': 'certification'}).status_code == 422
    assert service.upsert_count == 2


def test_skill_search_requires_authentication():
    with TestClient(create_app(Settings(enable_fixtures=False))) as client:
        assert client.get('/api/v1/skills?q=Python').status_code == 401


def test_explicit_ai_fallback_suggests_valid_category_without_a_goal_or_saving():
    import anyio
    from test_acceptance_m5_worker import CAPABILITY_ENTITY_ID
    from test_chat import FakeLlm, _service

    from jobtology_be.llm.client import LlmMessage
    user_text = 'FastAPI로 게시판 API 프로젝트를 직접 만들고 테스트했어요.'
    code = CAPABILITY_ENTITY_ID.rsplit(':', 1)[-1]
    llm = FakeLlm({'reply': '프로젝트 유형으로 정리해 볼까요?', 'candidates': [
        {'code': code, 'evidence_quote': user_text, 'category': 'project'},
    ]})
    result = anyio.run(_service(llm, None).respond, UUID(int=1), (LlmMessage('user', user_text),), 'spec')
    assert result.candidates[0].category == 'project'
    assert result.candidates[0].evidence_quote == user_text
    assert result.occupation_id is None


def test_capability_writes_return_503_when_ncs_catalog_is_unavailable():
    service = RecordingCapabilityService()
    app = create_app(Settings(_env_file=None, environment='test'), dependencies=ApiDependencies(
        identity_provider=StaticIdentityProvider(), capability_service=service,
        skill_catalog=SkillCatalog(lambda: ()),
    ))
    payload = {'expected_profile_version': 3, 'category': 'project', 'raw_text': '로그인 프로젝트',
               'proficiency': 'GUIDED', 'details': {'competency_ids': [NCS], 'evidence': EVIDENCE}}
    with TestClient(app) as client:
        for method, path in [('POST', '/api/v1/me/capabilities'),
                             ('PATCH', '/api/v1/me/capabilities/00000000-0000-0000-0000-000000000001')]:
            response = client.request(method, path, json=payload)
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'DATA_UNAVAILABLE'
    assert service.upsert_count == 0


@pytest.mark.parametrize(('category', 'fields'), [
    ('certification', {'issuer': '한국산업인력공단'}),
    ('test', {'score': 900, 'issuer': 'ETS'}),
])
def test_credentials_do_not_require_or_infer_a_performance_level(category, fields):
    service = RecordingCapabilityService()
    app = create_app(Settings(enable_fixtures=False), dependencies=ApiDependencies(
        identity_provider=StaticIdentityProvider(), capability_service=service, skill_catalog=catalog(),
    ))
    payload = {'expected_profile_version': 3, 'category': category, 'raw_text': '자격·시험 기록',
               'proficiency': None, 'details': {'competency_ids': [NCS], 'evidence': EVIDENCE, **fields}}
    with TestClient(app) as client:
        assert client.post('/api/v1/me/capabilities', json=payload).status_code == 201
        assert service.command.proficiency is None
        for experience_category in ('skill', 'project', 'experience'):
            assert client.post('/api/v1/me/capabilities', json={**payload, 'category': experience_category}).status_code == 422
    # A credential alone is not a claimed performance level or verified competency.
    assert spec_credit(SpecDetails.model_validate(payload['details']), None, category, TODAY) == 0
