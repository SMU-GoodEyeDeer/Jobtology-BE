from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_auth_session_api import FRONTEND_ORIGIN, RecordingSessionStore
from test_weekly_plan import context, production_steps, solver_steps

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import (
    AuthenticatedPrincipal,
    SessionIdentityProvider,
    require_authenticated_principal,
)
from jobtology_be.api.product_queries import require_product_queries
from jobtology_be.application.queries import RoadmapView
from jobtology_be.application.weekly_plan_models import PlanOverrides
from jobtology_be.infrastructure.persistence.auth_contracts import SessionPrincipal
from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    PersistenceConflictError,
)
from jobtology_be.main import create_app
from jobtology_be.modules.auth.session import issue_session_credentials
from jobtology_be.modules.auth.session_cookies import SESSION_COOKIE_NAME
from jobtology_be.settings import Settings

USER = UUID(int=200)
ROADMAP = context().roadmap_id
PATH = f'/api/v1/roadmaps/{ROADMAP}/plan'


class Identity:
    async def current_principal(self):
        return AuthenticatedPrincipal(user_id=USER)


class PlanSessions(RecordingSessionStore):
    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None:
        result = await super().resolve_session(session_token_hash)
        return replace(result, user_id=USER) if result else None


class Queries:
    async def get_roadmap(self, user_id, roadmap_id):
        assert user_id == USER
        if roadmap_id != ROADMAP:
            raise MissingRecordError(resource='roadmap')
        return RoadmapView(ROADMAP, 1, 'ACTIVE', UUID(int=201), UUID(int=202), '로드맵',
                           1, 'test-release', {}, tuple(s.step for s in production_steps()))


class Metadata:
    release_id = "test-release"
    def lookup(self, requirement_key):
        return next((s.metadata for s in production_steps()
                     if requirement_key == f'BACKEND_DEVELOPER:{s.step.step_key}'), None)


class Store:
    saved: PlanOverrides | None = None

    async def get_context(self, user_id, roadmap_id):
        assert user_id == USER
        if roadmap_id != ROADMAP:
            raise MissingRecordError(resource='roadmap')
        return context()

    async def get(self, user_id, roadmap_id):
        await self.get_context(user_id, roadmap_id)
        return self.saved

    async def upsert(self, user_id, roadmap_id, plan):
        await self.get_context(user_id, roadmap_id)
        if plan.version != (self.saved.version if self.saved else 0):
            raise PersistenceConflictError(resource='weekly plan')
        self.saved = plan.model_copy(update={'version': plan.version + 1})
        return self.saved

    async def delete(self, user_id, roadmap_id, expected_version):
        await self.get_context(user_id, roadmap_id)
        return await self.upsert(user_id, roadmap_id, PlanOverrides(
            weekly_hours=10, rest_weeks=(), order=(), hours={}, saved_at=None,
            version=expected_version, is_customized=False,
        ))


def app_with_store(store, metadata=True):
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), weekly_plan_store=store,
        requirement_metadata=Metadata() if metadata else None,
    ))
    app.dependency_overrides[require_product_queries] = lambda: Queries()
    return app


def payload():
    return {'expected_version': 0, 'weekly_hours': 12, 'rest_weeks': [11, 10],
            'order': [str(s.step.step_id) for s in production_steps()],
            'hours': {str(UUID(int=10)): 24, str(UUID(int=1)): 15}}


def test_plan_get_put_get_delete_round_trip():
    # Given
    store = Store()
    # When
    with TestClient(app_with_store(store)) as client:
        recommended = client.get(PATH)
        saved = client.put(PATH, json=payload())
        fetched = client.get(PATH)
        reset = client.delete(PATH, params={'expected_version': fetched.json()['version']})
    # Then
    assert recommended.status_code == saved.status_code == fetched.status_code == reset.status_code == 200
    assert recommended.json()['completion_date'] == '2027-04-12'
    assert saved.json() == fetched.json()
    assert saved.json()['rest_weeks'] == [10, 11]
    assert saved.json()['is_customized'] is True
    assert datetime.fromisoformat(saved.json()['saved_at']).utcoffset().total_seconds() == 32400
    assert reset.json() == recommended.json() | {'version': 2}


def test_put_stores_only_different_hours():
    # Given
    store = Store()
    # When
    with TestClient(app_with_store(store)) as client:
        response = client.put(PATH, json=payload())
    # Then
    assert response.status_code == 200
    assert store.saved is not None
    assert store.saved.hours == {str(UUID(int=10)): 24}


def test_preview_uses_same_schedule_as_save_without_persisting_overrides():
    store = Store()
    with TestClient(app_with_store(store)) as client:
        preview = client.post(f'{PATH}/preview', json=payload())
        assert store.saved is None
        saved = client.put(PATH, json=payload())
        original = client.post(f'{PATH}/preview', json=None)
        fetched = client.get(PATH)
    assert preview.status_code == saved.status_code == original.status_code == 200
    assert preview.json() == saved.json() | {'saved_at': None, 'version': 0}
    assert fetched.json() == saved.json()
    assert original.json()['is_customized'] is False
    assert original.json()['saved_at'] is None


def test_custom_deadline_overrun_is_saved_and_returned_with_warning():
    class DeadlineStore(Store):
        async def get_context(self, user_id, roadmap_id):
            return replace(await super().get_context(user_id, roadmap_id),
                           target_date=date(2026, 10, 20))

    store = DeadlineStore()
    with TestClient(app_with_store(store)) as client:
        response = client.put(PATH, json=payload())
    assert response.status_code == 200
    assert store.saved is not None
    assert response.json()['deadline_exceeded'] is True
    assert response.json()['days_past_target'] > 0
    assert any(w['code'] == 'TARGET_DATE_EXCEEDED' for w in response.json()['warnings'])


def test_preview_null_and_reset_return_exact_original_solver_dates():
    class SolverQueries(Queries):
        async def get_roadmap(self, user_id, roadmap_id):
            original = await super().get_roadmap(user_id, roadmap_id)
            return replace(original, steps=tuple(item.step for item in solver_steps()))

    store = Store()
    app = app_with_store(store)
    app.dependency_overrides[require_product_queries] = lambda: SolverQueries()
    inputs = payload() | {'order': [str(item.step.step_id) for item in solver_steps()], 'hours': {}}
    with TestClient(app) as client:
        original = client.get(PATH)
        customized = client.put(PATH, json=inputs)
        preview = client.post(f'{PATH}/preview', json=None)
        reset = client.delete(PATH, params={'expected_version': customized.json()['version']})
    assert original.status_code == customized.status_code == preview.status_code == reset.status_code == 200
    assert original.json()['completion_date'] == '2026-10-20'
    assert original.json()['schedule_source'] == 'SOLVER'
    assert customized.json()['schedule_source'] == 'CUSTOM'
    assert customized.json()['recommended_completion_date'] == '2026-10-20'
    assert original.json() | {'version': 1} == preview.json()
    assert original.json() | {'version': 2} == reset.json()


def test_preview_validates_request_without_saving():
    store = Store()
    with TestClient(app_with_store(store)) as client:
        response = client.post(f'{PATH}/preview', json=payload() | {'order': []})
    assert response.status_code == 422
    assert store.saved is None


def test_put_recommendation_values_removes_old_customization():
    store = Store()
    original_inputs = payload() | {'weekly_hours': 10, 'rest_weeks': [], 'hours': {}}
    with TestClient(app_with_store(store)) as client:
        original = client.get(PATH)
        client.put(PATH, json=payload())
        assert store.saved is not None
        response = client.put(PATH, json=original_inputs | {'expected_version': 1})
    assert response.status_code == 200
    assert response.json() == original.json() | {'version': 2}
    assert store.saved is not None and not store.saved.is_customized


def test_preview_requires_authentication():
    client = TestClient(create_app(Settings()))
    response = client.post(f'{PATH}/preview', json=payload())
    assert response.status_code == 401


@pytest.mark.parametrize('patch', [
    {'order': []}, {'order': [str(UUID(int=1))] * 11},
    {'hours': {str(UUID(int=999)): 20}}, {'weekly_hours': True}, {'weekly_hours': '12'},
    {'weekly_hours': 0}, {'weekly_hours': 61}, {'rest_weeks': [1, 1]},
    {'rest_weeks': [0]}, {'rest_weeks': [105]}, {'hours': {str(UUID(int=1)): 401}},
    {'hours': {str(UUID(int=1)): False}}, {'extra': 'forbidden'},
])
def test_invalid_put_returns_422_without_saving(patch):
    # Given
    store = Store()
    # When
    with TestClient(app_with_store(store)) as client:
        response = client.put(PATH, json=payload() | patch)
    # Then
    assert response.status_code == 422
    assert store.saved is None


@pytest.mark.parametrize('method', ['get', 'put', 'delete'])
def test_missing_roadmap_returns_not_found(method):
    # Given
    client = TestClient(app_with_store(Store()))
    # When
    response = client.request(method, f'/api/v1/roadmaps/{UUID(int=999)}/plan',
                              json=payload() if method == 'put' else None, params={'expected_version': 0})
    # Then
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'NOT_FOUND'


def test_metadata_unavailable_returns_fallback_plan_not_503():
    # Given
    client = TestClient(app_with_store(Store(), metadata=False))
    # When
    response = client.get(PATH)
    # Then
    assert response.status_code == 200
    assert response.json()['steps'][0]['ncs_level'] is None
    assert response.json()['steps'][0]['recommended_hours'] == 20


@pytest.mark.parametrize('method', ['get', 'put', 'delete'])
def test_plan_requires_authentication(method):
    # Given
    client = TestClient(create_app(Settings()))
    # When
    response = client.request(method, PATH, json=payload() if method == 'put' else None, params={'expected_version': 0})
    # Then
    assert response.status_code == 401


@pytest.mark.parametrize('method,path', [('put', PATH), ('delete', PATH), ('post', f'{PATH}/preview')])
def test_mutations_require_session_csrf_and_trusted_origin(method, path):
    # Given
    credentials = issue_session_credentials()
    app = app_with_store(Store())
    del app.dependency_overrides[require_authenticated_principal]
    app.dependency_overrides[require_authenticated_principal] = SessionIdentityProvider(
        session_store=PlanSessions(credentials), trusted_origins=frozenset({FRONTEND_ORIGIN}),
    ).current_principal
    # When
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE_NAME, credentials.cookie_value)
        missing = client.request(method, path, json=payload() if method in ('put', 'post') else None)
        hostile = client.request(method, path, json=payload() if method in ('put', 'post') else None,
                                 headers={'Origin': 'https://attacker.example',
                                          'X-CSRF-Token': credentials.csrf_token})
        accepted = client.request(method, path, json=payload() if method in ('put', 'post') else None,
                                  params={'expected_version': 0}, headers={'Origin': FRONTEND_ORIGIN,
                                           'X-CSRF-Token': credentials.csrf_token})
    # Then
    assert missing.status_code == hostile.status_code == 403
    assert accepted.status_code == 200


def test_stale_weekly_plan_save_and_reset_conflict_including_after_reset():
    with TestClient(app_with_store(Store())) as client:
        assert client.get(PATH).json()['version'] == 0
        first = client.put(PATH, json=payload())
        assert first.json()['version'] == 1
        assert client.put(PATH, json=payload()).status_code == 409
        assert client.delete(PATH, params={'expected_version': 0}).status_code == 409
        reset = client.delete(PATH, params={'expected_version': 1})
        assert reset.json()['version'] == 2 and not reset.json()['is_customized']
        assert client.put(PATH, json=payload()).status_code == 409
        assert client.put(PATH, json=payload() | {'expected_version': 2}).json()['version'] == 3


def test_completed_dependent_with_todo_prerequisite_supports_get_put_and_preview():
    class CompletedDependentQueries(Queries):
        async def get_roadmap(self, user_id, roadmap_id):
            roadmap = await super().get_roadmap(user_id, roadmap_id)
            first, second = roadmap.steps[:2]
            second = replace(second, state='COMPLETED', prerequisite_step_ids=(first.step_id,))
            return replace(roadmap, steps=(first, second))

    store = Store()
    store.saved = PlanOverrides(weekly_hours=12, rest_weeks=(), order=(), hours={},
                                saved_at=None, version=1)
    app = app_with_store(store)
    app.dependency_overrides[require_product_queries] = lambda: CompletedDependentQueries()
    request = {'expected_version': 1, 'weekly_hours': 12, 'rest_weeks': [],
               'order': [str(UUID(int=1)), str(UUID(int=2))], 'hours': {}}
    with TestClient(app) as client:
        fetched = client.get(PATH)
        previewed = client.post(f'{PATH}/preview', json=request)
        saved = client.put(PATH, json=request)
        reloaded = client.get(PATH)
    for response in (fetched, previewed, saved, reloaded):
        assert response.status_code == 200
        assert [step['state'] for step in response.json()['steps']] == ['TODO', 'COMPLETED']
        assert response.json()['total_hours'] == 35


def test_completed_hours_cannot_be_changed_by_an_override():
    class CompletedQueries(Queries):
        async def get_roadmap(self, user_id, roadmap_id):
            roadmap = await super().get_roadmap(user_id, roadmap_id)
            return replace(roadmap, steps=tuple(replace(step, state='COMPLETED')
                                                if step.step_id == UUID(int=1) else step for step in roadmap.steps))

    app = app_with_store(Store())
    app.dependency_overrides[require_product_queries] = lambda: CompletedQueries()
    with TestClient(app) as client:
        response = client.put(PATH, json=payload() | {'hours': {str(UUID(int=1)): 99}})
    assert response.status_code == 422
