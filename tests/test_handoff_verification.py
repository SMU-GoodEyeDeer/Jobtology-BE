from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

_SETTINGS_ENVIRONMENT_VARIABLES = (
    "JOBTOLOGY_ENVIRONMENT",
    "JOBTOLOGY_DATABASE_URL",
    "JOBTOLOGY_CORPUS_SNAPSHOT_PATH",
    "JOBTOLOGY_DB_LINK",
    "JOBTOLOGY_DB_PASSWORD",
    "JOBTOLOGY_DB_PROTOCOL",
    "JOBTOLOGY_ENABLE_FIXTURES",
    "JOBTOLOGY_ENABLE_FE_MOCK_SAMPLES",
    "JOBTOLOGY_CORS_ORIGINS",
    "JOBTOLOGY_AUTH_ENABLED",
    "JOBTOLOGY_GOOGLE_CLIENT_ID",
    "JOBTOLOGY_GOOGLE_CLIENT_SECRET",
    "JOBTOLOGY_GOOGLE_REDIRECT_URI",
    "JOBTOLOGY_FRONTEND_URL",
    "JOBTOLOGY_SESSION_TTL_SECONDS",
    "JOBTOLOGY_WORKER_BATCH_LIMIT",
)
_SYNTHETIC_DB_LINK = "neo4j://synthetic-user:synthetic-link-password@graph.example.test:7687"
_SYNTHETIC_DB_PASSWORD = "synthetic-db-password"
_SYNTHETIC_DATABASE_URL = "postgresql+asyncpg://app@127.0.0.1:5432/jobtology"


def _isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for variable in _SETTINGS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)


def test_handoff_serves_local_contract_surfaces_and_preserves_fail_closed_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    _isolate_settings(monkeypatch, tmp_path)
    from jobtology_be.api.analyses import AnalysisRequest, AnalysisRequestResponse
    from jobtology_be.api.errors import ErrorResponse
    from jobtology_be.api.roadmap_models import RoadmapCreateRequest
    from jobtology_be.main import create_app
    from jobtology_be.settings import Settings

    app = create_app(Settings())

    # When
    with TestClient(app) as client:
        docs_response = client.get("/api/docs")
        redoc_response = client.get("/api/redoc")
        openapi_response = client.get("/api/openapi.json")
        guide_response = client.get("/api/guide")
        legacy_statuses = [
            client.get(path).status_code
            for path in ("/docs", "/redoc", "/openapi.json", "/api-guide")
        ]
        default_mock_response = client.get("/api/v1/dev/mock/samples")
        default_fixture_response = client.get("/api/v1/dev/analysis")
        protected_response = client.get(
            "/api/v1/me/capabilities", headers={"X-User-Id": "untrusted"}
        )
        analysis_response = client.post(
            "/api/v1/analyses",
            json={
                "goal_id": "00000000-0000-0000-0000-000000000001",
                "expected_profile_version": 3,
                "basis_type": "EDITORIAL",
            },
        )

    # Then
    assert docs_response.status_code == 200
    assert redoc_response.status_code == 200
    assert openapi_response.status_code == 200
    assert guide_response.status_code == 200
    assert legacy_statuses == [404, 404, 404, 404]
    assert 'href="/api/docs"' in guide_response.text
    assert 'href="/api/redoc"' in guide_response.text
    assert 'href="/api/openapi.json"' in guide_response.text
    assert default_mock_response.status_code == 404
    assert default_fixture_response.status_code == 404
    assert protected_response.status_code == 401
    assert protected_response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert analysis_response.status_code == 401
    assert analysis_response.json()["error"]["code"] == "UNAUTHENTICATED"

    schema = openapi_response.json()
    assert "/api/guide" not in schema["paths"]
    analysis = schema["paths"]["/api/v1/analyses"]["post"]
    _ = AnalysisRequest.model_validate(
        schema["components"]["schemas"]["AnalysisRequest"]["examples"][0]
    )
    _ = AnalysisRequestResponse.model_validate(
        analysis["responses"]["202"]["content"]["application/json"]["example"]
    )
    _ = ErrorResponse.model_validate(
        schema["paths"]["/api/v1/health/live"]["get"]["responses"]["422"]["content"]
        ["application/json"]["example"]
    )
    _ = RoadmapCreateRequest.model_validate(
        schema["components"]["schemas"]["RoadmapCreateRequest"]["examples"][0]
    )


def test_handoff_mock_samples_are_explicit_read_only_and_do_not_authenticate_products(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    _isolate_settings(monkeypatch, tmp_path)
    from jobtology_be.api.analyses import AnalysisRequest, AnalysisRequestResponse
    from jobtology_be.api.errors import ErrorResponse
    from jobtology_be.main import create_app
    from jobtology_be.settings import Settings

    app = create_app(Settings(enable_fe_mock_samples=True))

    # When
    with TestClient(app) as client:
        samples_response = client.get("/api/v1/dev/mock/samples")
        legacy_fixture_response = client.get("/api/v1/dev/analysis")
        product_response = client.get("/api/v1/me/capabilities")
        write_response = client.post("/api/v1/dev/mock/samples")

    # Then
    assert samples_response.status_code == 200
    samples = samples_response.json()
    assert samples["fixture_kind"] == "FE_MOCK_SAMPLES"
    assert samples["read_only"] is True
    assert samples["contract_scope"] == "MOCK_ONLY_NOT_PRODUCT_DATA"
    assert legacy_fixture_response.status_code == 404
    assert product_response.status_code == 401
    assert product_response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert write_response.status_code == 405
    _ = AnalysisRequest.model_validate(samples["analysis_request"])
    _ = AnalysisRequestResponse.model_validate(samples["analysis_accepted"])
    _ = AnalysisRequestResponse.model_validate(samples["idempotency_replay"])
    _ = ErrorResponse.model_validate(samples["unauthenticated"])
    _ = ErrorResponse.model_validate(samples["version_conflict"])


def test_handoff_redacts_synthetic_settings_errors_and_rejects_production_fixtures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    _isolate_settings(monkeypatch, tmp_path)
    from jobtology_be.settings import Settings
    from jobtology_be.workers.main import WorkerSettings

    invalid_settings = {
        "environment": "production",
        "database_url": _SYNTHETIC_DATABASE_URL,
        "enable_fixtures": True,
        "db_link": _SYNTHETIC_DB_LINK,
        "db_password": _SYNTHETIC_DB_PASSWORD,
    }

    # When
    with pytest.raises(ValidationError) as settings_error:
        _ = Settings.model_validate(invalid_settings)
    with pytest.raises(ValidationError) as worker_error:
        _ = WorkerSettings.model_validate(
            {"db_link": _SYNTHETIC_DB_LINK, "db_password": _SYNTHETIC_DB_PASSWORD}
        )
    with pytest.raises(ValidationError):
        _ = Settings(
            environment="production",
            database_url=_SYNTHETIC_DATABASE_URL,
            enable_fe_mock_samples=True,
        )

    # Then
    assert _SYNTHETIC_DB_LINK not in str(settings_error.value)
    assert _SYNTHETIC_DB_PASSWORD not in str(settings_error.value)
    assert _SYNTHETIC_DB_LINK not in str(worker_error.value)
    assert _SYNTHETIC_DB_PASSWORD not in str(worker_error.value)
