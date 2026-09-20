import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobtology_be.main import create_app
from jobtology_be.settings import Settings


def test_fixtures_are_explicitly_opt_in():
    client = TestClient(create_app(Settings(_env_file=None, enable_fixtures=False)))
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}
    assert client.get("/api/v1/dev/analysis").status_code == 404


def test_fixture_contracts_link_analysis_and_proposal():
    client = TestClient(create_app(Settings(_env_file=None, enable_fixtures=True)))
    analysis = client.get("/api/v1/dev/analysis").json()
    proposal = client.get("/api/v1/dev/route-proposal").json()
    assert analysis["is_fixture"] and proposal["is_fixture"]
    assert proposal["analysis_id"] == analysis["analysis_id"]
    assert analysis["total_count"] == len(analysis["requirements"])


def test_production_cannot_expose_fixtures():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", enable_fixtures=True)
