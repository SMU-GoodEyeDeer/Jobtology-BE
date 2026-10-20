from fastapi.testclient import TestClient
from test_live_source_api import POSTING, Feed, Identity

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.live_source_models import OccupationContextResponse
from jobtology_be.infrastructure.persistence.live_source_feed import LiveSourceReadError
from jobtology_be.main import create_app
from jobtology_be.settings import Settings


class ContextFeed(Feed):
    async def occupation_context(self, codes, limit, offset):
        self.calls.append(("context", ",".join(codes or ()), limit, offset))
        if self.failure:
            raise LiveSourceReadError(self.failure)
        return OccupationContextResponse.model_validate({
            "contract_version": "jobtology-inferred-occupation-context-v1",
            "basis": "METADATA_INFERRED", "assertion_kind": "MODEL_INFERRED",
            "competency_evidence": False, "filters": {"occupation_codes": codes},
            "limit": limit, "offset": offset, "total": 1,
            "summary": {"associations": 1, "postings": 1, "current_associations": 0,
                        "stale_associations": 1, "by_source": [
                            {"source_id": "jobkorea", "associations": 1, "postings": 1,
                             "current_associations": 0}]},
            "items": [{"posting_key": "jobkorea:42", "posting_identity": "source:42",
                       "source_id": "jobkorea", "occupation_scheme": "NCS",
                       "occupation_code": "20010202", "occupation_name": "응용SW엔지니어링",
                       "association_id": "association-1", "basis": "METADATA_INFERRED",
                       "assertion_kind": "MODEL_INFERRED", "review_status": "UNREVIEWED",
                       "stage": "STAGING", "confidence": None, "confidence_state": "UNASSESSED",
                       "source_freshness": "CURRENT", "catalog_freshness": "STALE",
                       "source_pins": {"run_id": "retained-run", "private_path": "/private/export"},
                       "catalog_pins": {"pin": "historical-ncs"},
                       "posting": {**POSTING, "posting_id": "jobkorea:42", "source_id": "jobkorea",
                                   "source_status": "CLOSED", "deadline_kind": "UNKNOWN",
                                   "source_provenance": {"source_id": "jobkorea", "run_id": "retained-run",
                                                         "data_as_of": "2026-10-08T00:00:00Z"}},
                       "reviewer": "private-reviewer"}],
        })


def application(feed, authenticated=True):
    return create_app(Settings(_env_file=None), dependencies=ApiDependencies(
        identity_provider=Identity() if authenticated else None, live_source_feed=feed))


def test_inferred_context_keeps_freshness_and_real_preview_without_private_fields():
    feed = ContextFeed()
    with TestClient(application(feed)) as client:
        response = client.get("/api/v2/live/occupation-context?occupation_codes=20010202,15-1252.00&limit=3&offset=2")
    assert response.status_code == 200
    assert feed.calls == [("context", "20010202,15-1252.00", 3, 2)]
    value = response.json()
    assert value["competency_evidence"] is False
    item = value["items"][0]
    assert item["review_status"] == "UNREVIEWED"
    assert item["catalog_freshness"] == "STALE"
    assert item["posting"]["source_status"] == "CLOSED"
    assert item["posting"]["source_provenance"]["run_id"] == "retained-run"
    assert "private" not in response.text


def test_role_context_uses_reviewed_role_codes_and_requires_authentication():
    feed = ContextFeed()
    with TestClient(application(feed)) as client:
        found = client.get("/api/v2/roles/BACKEND_DEVELOPER/posting-context?limit=3")
        missing = client.get("/api/v2/roles/UNKNOWN/posting-context")
    assert found.status_code == 200
    assert missing.status_code == 404
    assert feed.calls == [("context", "20010202,20010204", 3, 0)]
    with TestClient(application(feed, authenticated=False)) as client:
        assert client.get("/api/v2/live/occupation-context").status_code == 401
        assert client.get("/api/v2/roles/BACKEND_DEVELOPER/posting-context").status_code == 401


def test_context_rejects_ambiguous_or_invalid_filters_before_reading():
    feed = ContextFeed()
    with TestClient(application(feed)) as client:
        for query in ("occupation_codes=", "occupation_codes=bad", "occupation_codes=20010202,",
                      "occupation_codes=20010202&occupation_codes=20010204", "offset=-1", "limit=101",
                      "preview=true"):
            assert client.get(f"/api/v2/live/occupation-context?{query}").status_code == 422
        assert client.get("/api/v2/roles/BACKEND_DEVELOPER/posting-context?unknown=true").status_code == 422
    assert feed.calls == []


def test_missing_catalog_reader_returns_service_error_without_private_details():
    with TestClient(application(ContextFeed(failure=503))) as client:
        response = client.get("/api/v2/roles/BACKEND_DEVELOPER/posting-context")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
