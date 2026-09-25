import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from jobtology_be.api.analyses import AnalysisRequest, AnalysisRequestResponse
from jobtology_be.api.errors import ErrorResponse
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


def test_frontend_mock_samples_are_opt_in_and_validate_product_dtos() -> None:
    # Given
    app = create_app(Settings(_env_file=None, enable_fe_mock_samples=True))

    with TestClient(app) as client:
        # When
        response = client.get("/api/v1/dev/mock/samples")
        legacy_preview_response = client.get("/api/v1/dev/analysis")
        write_response = client.post("/api/v1/dev/mock/samples")

    # Then
    assert response.status_code == 200
    samples = response.json()
    assert samples["fixture_kind"] == "FE_MOCK_SAMPLES"
    assert samples["read_only"] is True
    _ = AnalysisRequest.model_validate(samples["analysis_request"])
    _ = AnalysisRequestResponse.model_validate(samples["analysis_accepted"])
    _ = AnalysisRequestResponse.model_validate(samples["idempotency_replay"])
    _ = ErrorResponse.model_validate(samples["unauthenticated"])
    _ = ErrorResponse.model_validate(samples["version_conflict"])
    assert samples["legacy_preview_paths"] == [
        "/api/v1/dev/analysis",
        "/api/v1/dev/route-proposal",
    ]
    assert legacy_preview_response.status_code == 404
    assert write_response.status_code == 405


def test_frontend_mock_samples_are_disabled_by_default() -> None:
    # Given
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        # When
        response = client.get("/api/v1/dev/mock/samples")

    # Then
    assert response.status_code == 404


def test_fixture_mode_preserves_fail_closed_product_routes() -> None:
    # Given
    app = create_app(Settings(_env_file=None, enable_fe_mock_samples=True))

    with TestClient(app) as client:
        # When
        response = client.get("/api/v1/me/capabilities")

    # Then
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_production_rejects_frontend_mock_samples() -> None:
    # When / Then
    with pytest.raises(ValidationError):
        _ = Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+asyncpg://app@127.0.0.1:5432/jobtology",
            enable_fe_mock_samples=True,
        )
