from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_auth_session_api import FRONTEND_ORIGIN, RecordingSessionStore
from test_weekly_plan import context, production_steps

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
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
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
                           1, None, {}, tuple(s.step for s in production_steps()))


class Metadata:
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
        self.saved = plan
        return plan

    async def delete(self, user_id, roadmap_id):
        await self.get_context(user_id, roadmap_id)
        self.saved = None


def app_with_store(store, metadata=True):
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), weekly_plan_store=store,
        requirement_metadata=Metadata() if metadata else None,
    ))
    app.dependency_overrides[require_product_queries] = lambda: Queries()
    return app


def payload():
    return {'weekly_hours': 12, 'rest_weeks': [11, 10],
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
        reset = client.delete(PATH)
    # Then
    assert recommended.status_code == saved.status_code == fetched.status_code == reset.status_code == 200
    assert recommended.json()['completion_date'] == '2027-04-18'
    assert saved.json() == fetched.json()
    assert saved.json()['rest_weeks'] == [10, 11]
    assert saved.json()['is_customized'] is True
    assert datetime.fromisoformat(saved.json()['saved_at']).utcoffset().total_seconds() == 32400
    assert reset.json() == recommended.json()


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
                              json=payload() if method == 'put' else None)
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
    response = client.request(method, PATH, json=payload() if method == 'put' else None)
    # Then
    assert response.status_code == 401


@pytest.mark.parametrize('method', ['put', 'delete'])
def test_mutations_require_session_csrf_and_trusted_origin(method):
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
        missing = client.request(method, PATH, json=payload() if method == 'put' else None)
        hostile = client.request(method, PATH, json=payload() if method == 'put' else None,
                                 headers={'Origin': 'https://attacker.example',
                                          'X-CSRF-Token': credentials.csrf_token})
        accepted = client.request(method, PATH, json=payload() if method == 'put' else None,
                                  headers={'Origin': FRONTEND_ORIGIN,
                                           'X-CSRF-Token': credentials.csrf_token})
    # Then
    assert missing.status_code == hostile.status_code == 403
    assert accepted.status_code == 200
