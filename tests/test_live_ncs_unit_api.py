import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.live_source_feed import PostgresLiveSourceFeed
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

UNIT: dict[str, JsonValue] = {
    'base_code': '2001020205', 'code': '2001020205_23v6', 'name': 'Unit',
    'level': 3, 'definition': 'Official definition',
}
SOURCES: list[JsonValue] = [{'source_id': 'ncs_competency', 'run_id': 'ncs-1', 'data_as_of': None}]
PAYLOAD: dict[str, JsonValue] = {'contract_version': 'hop-live-source-v1', 'sources': SOURCES, 'item': UNIT}
PATH = '/api/v2/live/ncs-units/2001020205'
type Calls = list[tuple[str, dict[str, str | int | date | None] | None]]
type Reader = tuple[TestClient, list[JsonValue], list[DBAPIError], Calls]


@dataclass(frozen=True, slots=True)
class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())


@dataclass(frozen=True, slots=True)
class Result:
    payload: JsonValue

    def scalar_one(self) -> str:
        return json.dumps(self.payload)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> Iterator[Reader]:
    calls: Calls = []
    payload: list[JsonValue] = [PAYLOAD]
    failures: list[DBAPIError] = []

    class Connection:
        @asynccontextmanager
        async def begin(self) -> AsyncGenerator[None]:
            yield

        async def execute(
            self, statement: TextClause, params: dict[str, str | int | date | None] | None = None,
        ) -> Result:
            calls.append((str(statement), params))
            if failures and params is not None:
                raise failures[0]
            return Result(payload[0])

    @asynccontextmanager
    async def connect(_: AsyncEngine) -> AsyncGenerator[Connection]:
        yield Connection()

    monkeypatch.setattr(AsyncEngine, 'connect', connect)
    catalog = PostgresSourceCatalog.create(
        'postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog',
    )
    app = create_app(Settings(), dependencies=ApiDependencies(
        identity_provider=Identity(), live_source_feed=PostgresLiveSourceFeed(catalog.engine),
    ))
    with TestClient(app) as client:
        try:
            yield client, payload, failures, calls
        finally:
            if client.portal is not None:
                client.portal.call(catalog.dispose)


def test_ncs_unit_returns_safe_typed_detail_in_readonly_transaction(reader: Reader) -> None:
    # Given an authenticated restricted reader with extra source fields
    client, payload, _, calls = reader
    payload[0] = {**PAYLOAD, 'private': 'hidden', 'item': {**UNIT, 'raw': 'hidden'}}
    # When the NCS detail is read
    response = client.get(PATH)
    # Then only the declared contract is exposed through a bound read-only query
    assert response.status_code == 200
    assert response.json() == PAYLOAD
    assert calls == [
        ('SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY', None),
        ('SELECT CAST(catalog.live_ncs_unit_v1(:base_code) AS text)',
         {'base_code': '2001020205'}),
    ]


@pytest.mark.parametrize('item,expected,code', [
    (None, 404, 'NOT_FOUND'),
    ({**UNIT, 'level': '3'}, 503, 'DATA_UNAVAILABLE'),
    ({**UNIT, 'base_code': 'invalid'}, 503, 'DATA_UNAVAILABLE'),
])
def test_ncs_unit_maps_missing_or_malformed_db_item(
    reader: Reader, item: JsonValue, expected: int, code: str,
) -> None:
    # Given a missing or invalid DB item
    client, payload, _, _ = reader
    payload[0] = {**PAYLOAD, 'item': item}
    # When the detail is read
    response = client.get(PATH)
    # Then the existing public error envelope is used
    assert response.status_code == expected
    assert response.json()['error']['code'] == code


def test_ncs_unit_keeps_nullable_official_fields(reader: Reader) -> None:
    # Given a valid unit with unknown level and definition
    client, payload, _, _ = reader
    item = {**UNIT, 'level': None, 'definition': None}
    payload[0] = {**PAYLOAD, 'item': item}
    # When the detail is read
    response = client.get(PATH)
    # Then unknown official values stay null
    assert response.status_code == 200
    assert response.json()['item'] == item


@pytest.mark.parametrize('suffix', [
    'short', '200102020', '20010202055', '２００１０２０２０５',
    '2001020205?preview=true', '2001020205?x=1&x=2',
])
def test_ncs_unit_rejects_invalid_path_or_query_before_reading(reader: Reader, suffix: str) -> None:
    # Given a reader recording DB access
    client, _, _, calls = reader
    # When the path or query violates the contract
    response = client.get(f'/api/v2/live/ncs-units/{suffix}')
    # Then validation fails without touching the source
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'VALIDATION_ERROR'
    assert calls == []


def test_ncs_unit_database_failure_fails_closed(reader: Reader) -> None:
    # Given a missing function or other SQL failure
    client, _, failures, _ = reader
    failures.append(DBAPIError('private SQL', {}, RuntimeError('function missing')))
    # When the detail is read
    response = client.get(PATH)
    # Then no database details leak
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'DATA_UNAVAILABLE'
    assert 'private SQL' not in response.text


@pytest.mark.parametrize('authenticated,status,code', [
    (False, 401, 'UNAUTHENTICATED'), (True, 503, 'DATA_UNAVAILABLE'),
])
def test_ncs_unit_requires_identity_and_configured_feed(
    authenticated: bool, status: int, code: str,
) -> None:
    # Given an unconfigured app with or without authentication
    dependencies = (ApiDependencies(identity_provider=Identity()) if authenticated
                    else ApiDependencies())
    app = create_app(Settings(), dependencies=dependencies)
    # When the detail is read
    with TestClient(app) as client:
        response = client.get(PATH)
    # Then the route fails closed with the existing convention
    assert response.status_code == status
    assert response.json()['error']['code'] == code
