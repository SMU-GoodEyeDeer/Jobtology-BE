from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, assert_never

from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.corpus.neo4j_client import Neo4jQueryApiReadClient
from jobtology_be.corpus.neo4j_client_config import (
    Neo4jConfiguredSource,
    Neo4jQueryApiConfig,
)
from jobtology_be.corpus.neo4j_models import Neo4jOccupationNode, Neo4jPublication
from jobtology_be.corpus.neo4j_queries import NEO4J_CORPUS_READ_QUERY_CATALOG
from jobtology_be.corpus.neo4j_repository import (
    Neo4jCorpusReadRepository,
    Neo4jCorpusRepository,
    Neo4jNcsAlignmentWithCompetency,
    Neo4jPagination,
)
from jobtology_be.corpus.snapshot import (
    PublishedCorpusSnapshot,
    PublishedCorpusSnapshotReader,
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.corpus.source_availability import Available, SourceCapabilities, Unavailable
from jobtology_be.settings import CorpusSource


class ConfiguredCorpusSourceSettings(Neo4jConfiguredSource, Protocol):
    @property
    def corpus_source(self) -> CorpusSource: ...

    @property
    def corpus_snapshot_path(self) -> Path | None: ...


@dataclass(frozen=True, slots=True)
class _UnavailableSnapshotReader:
    async def get_snapshot(
        self, selection: PublishedSnapshotSelection
    ) -> PublishedCorpusSnapshot:
        raise PublishedSnapshotUnavailableError(selection=selection)


@dataclass(frozen=True, slots=True)
class ConfiguredNeo4jCatalog:
    """Safe native catalog projection backed by one configured, fixed-query repository."""

    capabilities: SourceCapabilities
    _repository: Neo4jCorpusReadRepository = field(repr=False)

    async def list_occupations(
        self, *, page: Neo4jPagination
    ) -> tuple[Neo4jOccupationNode, ...]:
        return await self._repository.list_occupations(page=page)

    async def list_publications(self, *, page: Neo4jPagination) -> tuple[Neo4jPublication, ...]:
        return await self._repository.list_publications(page=page)

    async def list_alignments(
        self, publication_id: str, *, page: Neo4jPagination
    ) -> tuple[Neo4jNcsAlignmentWithCompetency, ...]:
        return await self._repository.list_alignments(publication_id, page=page)


@dataclass(frozen=True, slots=True)
class ConfiguredCorpusSource:
    source: CorpusSource
    capabilities: SourceCapabilities
    _local_snapshot_reader: LocalJsonPublishedCorpusSnapshotReader | None
    _neo4j_client: Neo4jQueryApiReadClient | None
    _neo4j_catalog: ConfiguredNeo4jCatalog | None

    @property
    def local_snapshot_reader(self) -> LocalJsonPublishedCorpusSnapshotReader | None:
        match self.source:
            case "local_json":
                return self._local_snapshot_reader
            case "neo4j_query_api":
                return None
            case unreachable:
                assert_never(unreachable)

    @property
    def native_catalog(self) -> ConfiguredNeo4jCatalog | None:
        match self.source:
            case "local_json":
                return None
            case "neo4j_query_api":
                return self._neo4j_catalog
            case unreachable:
                assert_never(unreachable)

    @property
    def legacy_snapshot_reader(self) -> PublishedCorpusSnapshotReader:
        if self._local_snapshot_reader is None:
            return _UnavailableSnapshotReader()
        return self._local_snapshot_reader

    async def get_snapshot(
        self,
        *,
        source: CorpusSource,
        selection: PublishedSnapshotSelection,
    ) -> PublishedCorpusSnapshot:
        match source:
            case "local_json":
                return await self.legacy_snapshot_reader.get_snapshot(selection)
            case "neo4j_query_api":
                raise PublishedSnapshotUnavailableError(selection=selection)
            case unreachable:
                assert_never(unreachable)

    async def aclose(self) -> None:
        if self._neo4j_client is not None:
            await self._neo4j_client.aclose()


def build_configured_corpus_source(
    settings: ConfiguredCorpusSourceSettings,
) -> ConfiguredCorpusSource:
    local_snapshot_reader = _local_snapshot_reader(settings.corpus_snapshot_path)
    match settings.corpus_source:
        case "local_json":
            if local_snapshot_reader is None:
                unavailable = Unavailable(
                    status="UNAVAILABLE", reason="SOURCE_UNAVAILABLE"
                )
                capabilities = SourceCapabilities(
                    source="local_json",
                    catalog=unavailable,
                    editorial_analysis=unavailable,
                    route_planning=unavailable,
                )
            else:
                available = Available(status="AVAILABLE")
                capabilities = SourceCapabilities(
                    source="local_json",
                    catalog=available,
                    editorial_analysis=available,
                    route_planning=available,
                )
            return ConfiguredCorpusSource(
                source="local_json",
                capabilities=capabilities,
                _local_snapshot_reader=local_snapshot_reader,
                _neo4j_client=None,
                _neo4j_catalog=None,
            )
        case "neo4j_query_api":
            unavailable = Unavailable(
                status="UNAVAILABLE", reason="UNVERIFIED_SOURCE_CONTRACT"
            )
            capabilities = SourceCapabilities(
                source="neo4j_query_api",
                catalog=Available(status="AVAILABLE"),
                editorial_analysis=unavailable,
                route_planning=unavailable,
            )
            config = Neo4jQueryApiConfig.from_configured_source(settings)
            client = Neo4jQueryApiReadClient(
                config=config,
                catalog=NEO4J_CORPUS_READ_QUERY_CATALOG,
            )
            return ConfiguredCorpusSource(
                source="neo4j_query_api",
                capabilities=capabilities,
                _local_snapshot_reader=local_snapshot_reader,
                _neo4j_client=client,
                _neo4j_catalog=ConfiguredNeo4jCatalog(
                    capabilities=capabilities,
                    _repository=Neo4jCorpusRepository(
                        client=client,
                        max_rows=config.max_rows,
                    ),
                ),
            )
        case unreachable:
            assert_never(unreachable)


def _local_snapshot_reader(
    snapshot_path: Path | None,
) -> LocalJsonPublishedCorpusSnapshotReader | None:
    if snapshot_path is None:
        return None
    return LocalJsonPublishedCorpusSnapshotReader.from_path(snapshot_path)
