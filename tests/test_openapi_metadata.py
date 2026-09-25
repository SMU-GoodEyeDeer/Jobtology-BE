from pathlib import Path

import pytest

SETTINGS_ENVIRONMENT_VARIABLES = (
    "JOBTOLOGY_ENVIRONMENT",
    "JOBTOLOGY_DATABASE_URL",
    "JOBTOLOGY_CORPUS_SNAPSHOT_PATH",
    "JOBTOLOGY_CORPUS_SOURCE",
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


def test_openapi_documents_the_authenticated_async_roadmap_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    for variable in SETTINGS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)
    from jobtology_be.api.analyses import AnalysisRequest, AnalysisRequestResponse
    from jobtology_be.api.errors import ErrorResponse
    from jobtology_be.api.roadmap_models import RoadmapCreateRequest
    from jobtology_be.main import create_app
    from jobtology_be.settings import Settings

    # When
    app = create_app(Settings())
    schema = app.openapi()

    # Then
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"
    assert schema["info"]["summary"]
    assert "/api/v1" in schema["info"]["description"]
    assert "X-CSRF-Token" in schema["info"]["description"]
    assert {tag["name"] for tag in schema["tags"]} >= {
        "analyses",
        "roadmaps",
        "authentication",
        "native catalog",
        "development fixtures",
    }

    analysis = schema["paths"]["/api/v1/analyses"]["post"]
    assert analysis["summary"] == "Queue analysis recomputation"
    assert "status_url" in analysis["description"]
    assert analysis["responses"]["202"]["content"]["application/json"]["example"] == {
        "recompute_request_id": "00000000-0000-0000-0000-000000000101",
        "state": "PENDING",
        "status_url": "/api/v1/recomputations/00000000-0000-0000-0000-000000000101",
    }
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["description"]
        for parameter in analysis["parameters"]
    )
    assert (
        analysis["responses"]["401"]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/ErrorResponse"}
    )
    assert schema["components"]["schemas"]["AnalysisRequest"]["examples"] == [
        {
            "goal_id": "00000000-0000-0000-0000-000000000001",
            "expected_profile_version": 3,
            "basis_type": "EDITORIAL",
        }
    ]
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

    native_occupations = schema["paths"]["/api/v2/occupations"]["get"]
    native_publications = schema["paths"]["/api/v2/publications"]["get"]
    native_alignments = schema["paths"]["/api/v2/publications/{publication_id}/alignments"]["get"]
    assert native_occupations["summary"] == "List native Neo4j occupations"
    assert native_publications["summary"] == "List native Neo4j publications"
    assert "not a user capability" in native_alignments["description"]
    assert (
        native_occupations["responses"]["503"]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/ErrorResponse"}
    )
    assert (
        native_occupations["responses"]["422"]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/ErrorResponse"}
    )
    assert "404" not in native_occupations["responses"]
    assert "404" not in native_publications["responses"]
    assert (
        native_alignments["responses"]["404"]["content"]["application/json"]["schema"]
        == {"$ref": "#/components/schemas/ErrorResponse"}
    )
    for native_operation in (native_occupations, native_publications, native_alignments):
        query_schemas = {
            parameter["name"]: parameter["schema"]
            for parameter in native_operation["parameters"]
            if parameter["in"] == "query"
        }
        assert query_schemas["limit"]["minimum"] == 1
        assert query_schemas["limit"]["maximum"] == 100
        assert query_schemas["limit"]["default"] == 100
        assert query_schemas["offset"]["minimum"] == 0
        assert query_schemas["offset"]["default"] == 0
    assert set(schema["components"]["schemas"]["Neo4jOccupationResponse"]["properties"]) == {
        "id",
        "code",
        "kind",
        "name",
    }
    assert set(schema["components"]["schemas"]["Neo4jPublicationResponse"]["properties"]) == {
        "publication_id",
        "source_state",
        "capabilities",
    }
    assert set(schema["components"]["schemas"]["Neo4jNcsAlignmentResponse"]["properties"]) == {
        "publication_id",
        "source_enrichment_id",
        "source_posting_id",
        "source_current",
        "accepted",
        "competency",
    }

    roadmap = schema["paths"]["/api/v1/roadmaps"]["post"]
    mutation = schema["paths"]["/api/v1/roadmaps/{roadmap_id}"]["patch"]
    assert roadmap["summary"] == "Create a draft roadmap"
    assert "DRAFT" in roadmap["description"]
    assert "ACTIVATE" in mutation["description"]
    assert schema["components"]["schemas"]["RoadmapCreateRequest"]["examples"]
    _ = RoadmapCreateRequest.model_validate(
        schema["components"]["schemas"]["RoadmapCreateRequest"]["examples"][0]
    )
