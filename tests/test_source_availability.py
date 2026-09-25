import pytest
from pydantic import ValidationError

from jobtology_be.corpus.source_availability import SourceCapabilities


def test_source_capabilities_reject_unavailable_without_reason() -> None:
    # Given: a source has not been verified, and no explanation is supplied.
    payload = {
        "source": "neo4j_query_api",
        "catalog": {"status": "UNAVAILABLE"},
        "editorial_analysis": {"status": "UNAVAILABLE", "reason": "UNVERIFIED_SOURCE_CONTRACT"},
        "route_planning": {"status": "UNAVAILABLE", "reason": "UNVERIFIED_SOURCE_CONTRACT"},
    }

    # When / Then: the public boundary rejects the ambiguous availability state.
    with pytest.raises(ValidationError):
        SourceCapabilities.model_validate(payload)


def test_source_capabilities_keep_catalog_independent_from_analysis() -> None:
    # Given: a readable catalog without a verified editorial or planning contract.
    payload = {
        "source": "neo4j_query_api",
        "catalog": {"status": "AVAILABLE"},
        "editorial_analysis": {"status": "UNAVAILABLE", "reason": "UNVERIFIED_SOURCE_CONTRACT"},
        "route_planning": {"status": "UNAVAILABLE", "reason": "UNVERIFIED_SOURCE_CONTRACT"},
    }

    # When: the public capability envelope is parsed.
    capabilities = SourceCapabilities.model_validate(payload)

    # Then: catalog availability does not silently claim analysis or planning support.
    assert capabilities.model_dump(mode="json") == payload


def test_source_capabilities_forbid_unreviewed_remote_fields() -> None:
    # Given: a remote record includes an unapproved reviewer property.
    payload = {
        "source": "neo4j_query_api",
        "catalog": {"status": "UNAVAILABLE", "reason": "SOURCE_UNAVAILABLE"},
        "editorial_analysis": {"status": "UNAVAILABLE", "reason": "SOURCE_UNAVAILABLE"},
        "route_planning": {"status": "UNAVAILABLE", "reason": "SOURCE_UNAVAILABLE"},
        "reviewer_email": "private@example.invalid",
    }

    # When / Then: the response boundary rejects the extra source property.
    with pytest.raises(ValidationError) as failure:
        SourceCapabilities.model_validate(payload)
    assert "private@example.invalid" not in str(failure.value)
