from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobtology_be.api.errors import register_error_handlers
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.api.m5_queries import require_m5_queries, router
from jobtology_be.application.m5_queries import M5DataUnavailableError, M5Queries


class UnavailableM5Queries(M5Queries):
    async def get_occupations(self):
        raise M5DataUnavailableError("occupation catalog")

    async def get_route_proposal(self, user_id, proposal_id):
        raise AssertionError

    async def get_trace(self, user_id, trace_id):
        raise AssertionError

    async def get_dashboard(self, user_id, goal_id):
        raise AssertionError


def test_occupations_returns_explicit_data_unavailable_response() -> None:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)

    async def principal() -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())

    app.dependency_overrides[require_authenticated_principal] = principal
    def queries() -> M5Queries:
        return UnavailableM5Queries()

    app.dependency_overrides[require_m5_queries] = queries

    with TestClient(app) as client:
        response = client.get("/occupations")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
