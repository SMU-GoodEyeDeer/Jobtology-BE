import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr

from jobtology_be.corpus.snapshot import (
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.corpus.source_factory import build_configured_corpus_source
from jobtology_be.settings import CorpusSource

SELECTION = PublishedSnapshotSelection(
    occupation_id="BACKEND_DEVELOPER",
    basis_version="reviewed-v1",
    release_id="release-reviewed-v1",
)


@dataclass(frozen=True, slots=True)
class StaticSourceSettings:
    corpus_source: CorpusSource
    corpus_snapshot_path: Path | None
    db_link: SecretStr | None = None
    db_password: SecretStr | None = None
    db_protocol: str | None = None


@pytest.mark.anyio
async def test_local_json_source_exposes_local_reader_and_available_capabilities(
    tmp_path: Path,
) -> None:
    # Given
    snapshot_path = tmp_path / "published-corpus.json"
    _ = snapshot_path.write_text('{"snapshots": []}')
    settings = StaticSourceSettings(
        corpus_source="local_json",
        corpus_snapshot_path=snapshot_path,
    )

    # When
    source = build_configured_corpus_source(settings)

    # Then
    assert source.local_snapshot_reader is not None
    assert source.native_catalog is None
    assert source.capabilities.source == "local_json"
    assert source.capabilities.catalog.status == "AVAILABLE"
    assert source.capabilities.editorial_analysis.status == "AVAILABLE"
    assert source.capabilities.route_planning.status == "AVAILABLE"
    await source.aclose()


@pytest.mark.anyio
async def test_neo4j_source_accepts_configured_host_and_protocol_without_executing_a_query() -> None:
    # Given
    settings = StaticSourceSettings(
        corpus_source="neo4j_query_api",
        corpus_snapshot_path=None,
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("synthetic-query-api-password"),
        db_protocol="bolt+s://",
    )
    source = build_configured_corpus_source(settings)

    # When / Then
    try:
        with pytest.raises(PublishedSnapshotUnavailableError):
            _ = await source.get_snapshot(source="neo4j_query_api", selection=SELECTION)
        assert source.local_snapshot_reader is None
        assert source.native_catalog is not None
        assert source.native_catalog.capabilities == source.capabilities
        assert source.capabilities.source == "neo4j_query_api"
        assert source.capabilities.catalog.status == "AVAILABLE"
        assert source.capabilities.editorial_analysis.status == "UNAVAILABLE"
        assert source.capabilities.route_planning.status == "UNAVAILABLE"
    finally:
        await source.aclose()


@pytest.mark.anyio
async def test_neo4j_mode_replays_a_legacy_local_selection_from_the_configured_local_file(
    tmp_path: Path,
) -> None:
    # Given
    snapshot_path = tmp_path / "published-corpus.json"
    _ = snapshot_path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "occupation_id": "BACKEND_DEVELOPER",
                        "basis_version": "reviewed-v1",
                        "release": {
                            "release_id": "release-reviewed-v1",
                            "state": "PUBLISHED",
                            "reviewed_at": "2026-09-22T00:00:00+00:00",
                        },
                        "is_fixture": False,
                        "capability_entries": [],
                        "allowed_experience_codes": [],
                        "requirements": [],
                        "templates": [],
                    }
                ]
            }
        )
    )
    source = build_configured_corpus_source(
        StaticSourceSettings(
            corpus_source="neo4j_query_api",
            corpus_snapshot_path=snapshot_path,
            db_link=SecretStr("graph.example.test:7687"),
            db_password=SecretStr("synthetic-query-api-password"),
            db_protocol="bolt+s://",
        )
    )

    # When
    try:
        snapshot = await source.get_snapshot(source="local_json", selection=SELECTION)
    finally:
        await source.aclose()

    # Then
    assert snapshot.occupation_id == SELECTION.occupation_id
    assert source.local_snapshot_reader is None
