from hashlib import sha256

import pytest
from pydantic import ValidationError

from jobtology_be.api.occupation_source_models import (
    Neo4jNcsAlignmentResponse,
    Neo4jOccupationResponse,
    Neo4jPublicationResponse,
)
from jobtology_be.corpus.neo4j_models import (
    Neo4jEnrichment,
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_repository import Neo4jNcsAlignmentSource
from jobtology_be.corpus.source_availability import Available, SourceCapabilities, Unavailable


def test_publication_preserves_distinct_observed_identifiers() -> None:
    # Given: separate source properties with deliberately different values.
    data = {"id": "source-node-1", "publication_id": "source-release-1", "postings": 9, "state": "READY"}

    # When: the source record is parsed.
    publication = Neo4jPublication.model_validate(data)

    # Then: the two identities remain distinct and the state remains source-native.
    assert (publication.id, publication.publication_id, publication.state) == (
        "source-node-1", "source-release-1", "READY"
    )


def test_publication_preserves_uninterpreted_postings_integer() -> None:
    # Given: the source contract establishes an integer, not count semantics.
    data = {"id": "node-1", "publication_id": "release-1", "postings": -1, "state": "READY"}

    # When: the source record is parsed.
    publication = Neo4jPublication.model_validate(data)

    # Then: the adapter leaves its business meaning uninterpreted.
    assert publication.postings == -1


def test_publication_rejects_unobserved_properties() -> None:
    # Given: a source shape with unexpected extra content.
    data = {"id": "node-1", "publication_id": "release-1", "postings": 9, "state": "READY", "reviewer": "private"}

    # When / Then: the boundary rejects rather than normalizing the record.
    with pytest.raises(ValidationError):
        Neo4jPublication.model_validate(data)


def test_enrichment_hashes_original_utf8_without_exposing_raw_payload() -> None:
    # Given: JSON whitespace and Unicode that differ after parsing and reserialization.
    raw_payload = (
        '{  "item_id": "item-1", "posting_id": "posting-1", "revision_id": "rev-1",'
        ' "source_hash": "source-sha", "extraction": {"duties_status": "complete",'
        ' "extraction_scope": "scope", "schema_version": "v1"}, "name": "역량" }'
    )
    data = {
        "id": "enrichment-1", "publication_id": "release-1", "posting_id": "posting-1",
        "current": True, "managed_by": "reviewer-private", "name": "private-title",
        "payload_json": raw_payload, "payload_hash": sha256(raw_payload.encode("utf-8")).hexdigest(),
    }

    # When: the source enrichment is parsed.
    enrichment = Neo4jEnrichment.model_validate(data)

    # Then: only source identity/currentness/hash metadata remains serializable.
    assert enrichment.model_dump() == {
        "id": "enrichment-1", "publication_id": "release-1", "posting_id": "posting-1",
        "current": True, "payload_hash": data["payload_hash"],
    }
    assert raw_payload not in repr(enrichment)


def test_enrichment_hash_mismatch_redacts_payload_in_error() -> None:
    # Given: a modified payload with its old hash.
    secret = "SYNTHETIC_TEST_SENTINEL_NOT_A_CREDENTIAL"
    data = {
        "id": "enrichment-1", "publication_id": "release-1", "posting_id": "posting-1",
        "current": False, "managed_by": "private", "name": "private",
        "payload_json": secret, "payload_hash": "0" * 64,
    }

    # When / Then: verification rejects it without including raw content in the error.
    with pytest.raises(ValidationError) as failure:
        Neo4jEnrichment.model_validate(data)
    assert secret not in str(failure.value)


def test_enrichment_rejects_malformed_payload_without_echoing_it() -> None:
    # Given: an intact hash over malformed source JSON.
    raw_payload = "private-reviewer:{"
    data = {
        "id": "enrichment-1", "publication_id": "release-1", "posting_id": "posting-1",
        "current": True, "managed_by": "private", "name": "private",
        "payload_json": raw_payload, "payload_hash": sha256(raw_payload.encode("utf-8")).hexdigest(),
    }

    # When / Then: the source boundary rejects the shape without exposing the raw text.
    with pytest.raises(ValidationError) as failure:
        Neo4jEnrichment.model_validate(data)
    assert raw_payload not in str(failure.value)


def test_enrichment_rejects_wrong_extraction_status_type() -> None:
    # Given: valid JSON and hash, but an observed status field has the wrong type.
    raw_payload = (
        '{"item_id":"item-1","posting_id":"posting-1","revision_id":"rev-1",'
        '"source_hash":"sha","extraction":{"duties_status":[],"extraction_scope":"scope",'
        '"schema_version":"v1"}}'
    )
    data = {
        "id": "enrichment-1", "publication_id": "release-1", "posting_id": "posting-1",
        "current": True, "managed_by": "private", "name": "private",
        "payload_json": raw_payload, "payload_hash": sha256(raw_payload.encode("utf-8")).hexdigest(),
    }

    # When / Then: the source boundary refuses an untyped payload rather than trusting hash alone.
    with pytest.raises(ValidationError):
        Neo4jEnrichment.model_validate(data)


def test_public_occupation_whitelists_only_catalog_fields() -> None:
    # Given: the observed source catalog shape, including private provenance IDs.
    node = Neo4jOccupationNode.model_validate({
        "id": "ncs-1", "code": "NCS-CODE", "kind": "occupation", "name": "Source label",
        "name_source_record_id": "internal-record", "name_source_run_id": "internal-run",
    })

    # When: the public record is projected from the validated source node.
    response = Neo4jOccupationResponse.from_source(node)

    # Then: identifiers/labels are preserved without private provenance or synthetic mapping.
    assert response.model_dump() == {
        "id": "ncs-1", "code": "NCS-CODE", "kind": "occupation", "name": "Source label",
    }
    with pytest.raises(ValidationError) as failure:
        Neo4jOccupationResponse.model_validate({**response.model_dump(), "reviewer": "private"})
    assert "private" not in str(failure.value)


def test_source_competency_keeps_its_label_distinct_from_occupation() -> None:
    # Given: a competency catalog node with the same observed property shape.
    data = {
        "id": "competency-1", "code": "NCS-CODE", "kind": "ncsCompetency", "name": "Source label",
        "name_source_record_id": "internal-record", "name_source_run_id": "internal-run",
    }

    # When: the competency boundary parses it.
    node = Neo4jNcsCompetencyNode.model_validate(data)

    # Then: it remains a competency type, not an occupation instance.
    assert node.name == "Source label"
    assert not isinstance(node, Neo4jOccupationNode)


def test_public_alignment_exposes_safe_source_context_without_private_decision_details() -> None:
    # Given: source alignment context and a private decision identifier.
    competency = Neo4jNcsCompetencyNode.model_validate({
        "id": "competency-1", "code": "NCS-CODE", "kind": "ncsCompetency", "name": "Source label",
        "name_source_record_id": "internal-record", "name_source_run_id": "internal-run",
    })
    source_enrichment = Neo4jNcsAlignmentSource.model_validate({
        "id": "enrichment-1", "posting_id": "posting-1", "current": True,
    })
    alignment = Neo4jNcsAlignment.model_validate({
        "accepted": False, "decision_id": 42, "publication_id": "source-publication",
    })

    # When: the validated source relation is projected publicly.
    response = Neo4jNcsAlignmentResponse.from_source(source_enrichment, alignment, competency)

    # Then: source context is preserved; private reviewer and decision ID are absent.
    assert response.model_dump() == {
        "accepted": False,
        "publication_id": "source-publication",
        "source_enrichment_id": "enrichment-1",
        "source_posting_id": "posting-1",
        "source_current": True,
        "competency": {"id": "competency-1", "code": "NCS-CODE", "kind": "ncsCompetency", "name": "Source label"},
    }
    assert "decision_id" not in response.model_dump()
    with pytest.raises(ValidationError):
        Neo4jNcsAlignmentResponse.model_validate({**response.model_dump(), "reviewer": "private"})


def test_public_publication_keeps_source_state_separate_from_capabilities() -> None:
    # Given: a real source publication without a reviewed editorial planner contract.
    publication = Neo4jPublication.model_validate({
        "id": "source-node", "publication_id": "source-release", "postings": 9, "state": "READY",
    })
    capabilities = SourceCapabilities(
        source="neo4j_query_api", catalog=Available(status="AVAILABLE"),
        editorial_analysis=Unavailable(status="UNAVAILABLE", reason="UNVERIFIED_SOURCE_CONTRACT"),
        route_planning=Unavailable(status="UNAVAILABLE", reason="UNVERIFIED_SOURCE_CONTRACT"),
    )

    # When: safe source publication metadata is projected.
    response = Neo4jPublicationResponse.from_source(publication, capabilities)

    # Then: READY is source state, not a manufactured app release or posting count.
    result = response.model_dump(mode="json")
    assert result["source_state"] == "READY"
    assert result["publication_id"] == "source-release"
    assert result["capabilities"]["editorial_analysis"]["status"] == "UNAVAILABLE"
    assert "release_id" not in result and "postings" not in result and "id" not in result
