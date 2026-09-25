from os import environ
from urllib.parse import urlsplit

import pytest

from jobtology_be.corpus.neo4j_client_config import Neo4jQueryApiConfig
from jobtology_be.corpus.neo4j_repository import Neo4jPagination
from jobtology_be.corpus.source_factory import build_configured_corpus_source
from jobtology_be.settings import Settings

_LIVE_ACCEPTANCE_ENV = "JOBTOLOGY_NEO4J_LIVE_ACCEPTANCE"
_EXPECTED_NEO4J_HOST = "neo4j-1.yeongmin.net"
_HEX_DIGITS = frozenset("0123456789abcdef")


def _native_settings() -> Settings:
    if environ.get(_LIVE_ACCEPTANCE_ENV) != "1":
        pytest.skip(f"set {_LIVE_ACCEPTANCE_ENV}=1 for the authorized read-only Neo4j check")
    configured = Settings()
    if configured.db_link is None or configured.db_password is None:
        pytest.skip("typed runtime settings do not contain Neo4j credentials")
    settings = configured.model_copy(
        update={
            "auth_enabled": False,
            "corpus_snapshot_path": None,
            "corpus_source": "neo4j_query_api",
            "database_url": None,
        }
    )
    endpoint = urlsplit(Neo4jQueryApiConfig.from_configured_source(settings).endpoint)
    assert endpoint.scheme == "https"
    assert endpoint.hostname == _EXPECTED_NEO4J_HOST
    return settings


@pytest.mark.anyio
async def test_live_repository_verifies_three_bounded_source_hashes() -> None:
    source = build_configured_corpus_source(_native_settings())
    catalog = source.native_catalog
    assert catalog is not None
    try:
        publications = await catalog.list_publications(page=Neo4jPagination(limit=1, offset=0))
        assert len(publications) == 1
        publication_id = publications[0].publication_id
        alignments = await catalog.list_alignments(
            publication_id, page=Neo4jPagination(limit=3, offset=0)
        )
        assert len(alignments) == 3
        enrichment_ids = tuple(alignment.source_enrichment.id for alignment in alignments)
        enrichments = tuple(
            [
                await catalog._repository.get_enrichment(publication_id, enrichment_id)
                for enrichment_id in enrichment_ids
            ]
        )
    finally:
        await source.aclose()

    hashes_are_verified = all(
        enrichment is not None
        and enrichment.publication_id == publication_id
        and enrichment.id == enrichment_id
        and len(enrichment.payload_hash) == 64
        and set(enrichment.payload_hash).issubset(_HEX_DIGITS)
        for enrichment_id, enrichment in zip(enrichment_ids, enrichments, strict=True)
    )
    assert hashes_are_verified
