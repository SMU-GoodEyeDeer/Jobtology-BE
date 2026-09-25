from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jobtology_be.application.services.analysis_inputs import (
    AnalysisContextInputsUnavailableError,
)
from jobtology_be.contracts import CapabilityInput
from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyConflictError,
    MissingRecordError,
    OwnershipError,
    PersistenceConflictError,
)
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


def test_validation_error_omits_submitted_values() -> None:
    # Given
    app = create_app(Settings(enable_fixtures=False))

    @app.post("/test/capability")
    def accept_capability(body: CapabilityInput) -> CapabilityInput:
        return body

    with TestClient(app) as client:
        # When
        response = client.post("/test/capability", json={"raw_text": "", "secret": "private"})

    # Then
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {tuple(item["location"]) for item in error["details"]} == {
        ("body", "raw_text"),
        ("body", "secret"),
    }
    assert all(set(item) == {"location", "code"} for item in error["details"])
    assert "private" not in response.text
    assert UUID(error["request_id"]).version == 4
    assert response.headers["x-request-id"] == error["request_id"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "UNAUTHENTICATED"), (403, "FORBIDDEN"), (503, "DATA_UNAVAILABLE")],
)
def test_http_errors_preserve_headers_without_exposing_details(status: int, code: str) -> None:
    # Given
    app = create_app(Settings(enable_fixtures=False))

    @app.get("/test/error")
    def raise_error() -> None:
        raise HTTPException(
            status_code=status,
            detail="private implementation detail",
            headers={"WWW-Authenticate": "Bearer", "Retry-After": "30"},
        )

    with TestClient(app) as client:
        # When
        response = client.get("/test/error")

    # Then
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["retry-after"] == "30"
    assert "private" not in response.text


def test_missing_route_returns_error_envelope() -> None:
    # Given
    with TestClient(create_app(Settings(enable_fixtures=False))) as client:
        # When
        response = client.get("/missing")

    # Then
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_method_not_allowed_preserves_allow_header() -> None:
    # Given
    with TestClient(create_app(Settings(enable_fixtures=False))) as client:
        # When
        response = client.post("/api/v1/health/live")

    # Then
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


def test_malformed_json_returns_safe_validation_error() -> None:
    # Given
    app = create_app(Settings(enable_fixtures=False))

    @app.post("/test/capability")
    def accept_capability(body: CapabilityInput) -> CapabilityInput:
        return body

    with TestClient(app) as client:
        # When
        response = client.post(
            "/test/capability",
            content='{"raw_text": "private",',
            headers={"Content-Type": "application/json"},
        )

    # Then
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["code"] == "json_invalid"
    assert "private" not in response.text


def test_openapi_declares_standard_validation_response() -> None:
    # Given
    with TestClient(create_app(Settings(enable_fixtures=False))) as client:
        # When
        response = client.get("/openapi.json")

    # Then
    schema = response.json()
    validation = schema["paths"]["/api/v1/health/live"]["get"]["responses"]["422"]
    assert validation["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse",
    }
    assert set(schema["components"]["schemas"]["ErrorBody"]["properties"]) == {
        "code",
        "message",
        "details",
        "request_id",
    }


def test_browser_can_read_server_generated_request_id() -> None:
    # Given
    settings = Settings(enable_fixtures=False, cors_origins=["http://localhost:5173"])
    with TestClient(create_app(settings)) as client:
        # When
        response = client.get("/missing", headers={"Origin": "http://localhost:5173"})

    # Then
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


@pytest.mark.parametrize(
    ("error", "status", "code", "private_value"),
    [
        (PersistenceConflictError(resource="profile"), 409, "VERSION_CONFLICT", "profile"),
        (IdempotencyConflictError(key="private-key"), 409, "IDEMPOTENCY_CONFLICT", "private-key"),
        (OwnershipError(resource="roadmap"), 403, "FORBIDDEN", "roadmap"),
        (MissingRecordError(resource="analysis"), 404, "NOT_FOUND", "analysis"),
        (
            AnalysisContextInputsUnavailableError("private route-preference state"),
            503,
            "DATA_UNAVAILABLE",
            "route-preference",
        ),
    ],
)
def test_persistence_errors_return_redacted_product_error_codes(
    error: Exception,
    status: int,
    code: str,
    private_value: str,
) -> None:
    # Given
    app = create_app(Settings(enable_fixtures=False))

    @app.get("/test/persistence-error")
    def raise_persistence_error() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        # When
        response = client.get("/test/persistence-error")

    # Then
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert private_value not in response.text
