"""Typed read-only repository surface for observed Neo4j corpus records."""

from dataclasses import dataclass, field
from typing import ClassVar, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from jobtology_be.corpus.neo4j_client import (
    Neo4jReadClient,
    Neo4jReadQuery,
)
from jobtology_be.corpus.neo4j_models import (
    Neo4jEnrichment,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_queries import (
    ALIGNMENT_RESULT_FIELDS,
    ENRICHMENT_FIELDS,
    GET_ENRICHMENT_QUERY_ID,
    GET_PUBLICATION_BY_ID_QUERY_ID,
    LIST_ALIGNMENTS_QUERY_ID,
    LIST_OCCUPATIONS_QUERY_ID,
    LIST_PUBLICATIONS_QUERY_ID,
    OCCUPATION_FIELDS,
    PUBLICATION_FIELDS,
)

from .neo4j_repository_support import (
    Neo4jDuplicateSourceIdentityError,
    Neo4jNcsAlignmentSource,
    Neo4jNcsAlignmentWithCompetency,
    Neo4jPublicationNotFoundError,
    Neo4jRepositoryError,
    Neo4jRepositoryRequestError,
    Neo4jRepositoryResponseError,
    _alignment_with_competency,
    _parse_records,
    _reject_duplicate_identities,
    _require_result_row_bound,
    _row_mappings,
)

_SELECTED_ENRICHMENT_ROW_LIMIT: Final = 2
_SELECTED_PUBLICATION_ROW_LIMIT: Final = 2

__all__ = (
    "Neo4jCorpusReadRepository",
    "Neo4jCorpusRepository",
    "Neo4jDuplicateSourceIdentityError",
    "Neo4jNcsAlignmentSource",
    "Neo4jNcsAlignmentWithCompetency",
    "Neo4jPagination",
    "Neo4jPublicationNotFoundError",
    "Neo4jRepositoryError",
    "Neo4jRepositoryRequestError",
    "Neo4jRepositoryResponseError",
)


class Neo4jPagination(BaseModel):
    """One bounded source page; repository limits are applied before issuing a query."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    limit: StrictInt = Field(gt=0)
    offset: StrictInt = Field(ge=0)


class Neo4jCorpusReadRepository(Protocol):
    """Read-only native-corpus queries available to API and worker composition."""

    async def list_occupations(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jOccupationNode, ...]: ...

    async def list_publications(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jPublication, ...]: ...

    async def list_alignments(
        self,
        publication_id: str,
        *,
        page: Neo4jPagination | None = None,
    ) -> tuple[Neo4jNcsAlignmentWithCompetency, ...]: ...

    async def get_enrichment(
        self, publication_id: str, enrichment_id: str
    ) -> Neo4jEnrichment | None: ...


@dataclass(frozen=True, slots=True)
class Neo4jCorpusRepository:
    client: Neo4jReadClient = field(repr=False)
    max_rows: StrictInt

    def __post_init__(self) -> None:
        if self.max_rows <= 0:
            raise Neo4jRepositoryRequestError()

    async def list_occupations(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jOccupationNode, ...]:
        selected_page = self._page_for(page)
        result = await self.client.execute(
            Neo4jReadQuery(
                query_id=LIST_OCCUPATIONS_QUERY_ID,
                parameters={"limit": selected_page.limit, "offset": selected_page.offset},
            )
        )
        _require_result_row_bound(result, selected_page.limit)
        occupations = _parse_records(
            result,
            fields=OCCUPATION_FIELDS,
            model_type=Neo4jOccupationNode,
        )
        _reject_duplicate_identities(tuple(occupation.id for occupation in occupations))
        return occupations

    async def list_publications(
        self, *, page: Neo4jPagination | None = None
    ) -> tuple[Neo4jPublication, ...]:
        selected_page = self._page_for(page)
        result = await self.client.execute(
            Neo4jReadQuery(
                query_id=LIST_PUBLICATIONS_QUERY_ID,
                parameters={"limit": selected_page.limit, "offset": selected_page.offset},
            )
        )
        _require_result_row_bound(result, selected_page.limit)
        publications = _parse_records(
            result,
            fields=PUBLICATION_FIELDS,
            model_type=Neo4jPublication,
        )
        _reject_duplicate_identities(
            tuple(publication.publication_id for publication in publications)
        )
        return publications

    async def list_alignments(
        self,
        publication_id: str,
        *,
        page: Neo4jPagination | None = None,
    ) -> tuple[Neo4jNcsAlignmentWithCompetency, ...]:
        source_publication_id = _require_nonempty_source_id(publication_id)
        selected_page = self._page_for(page)
        await self._require_publication(source_publication_id)
        result = await self.client.execute(
            Neo4jReadQuery(
                query_id=LIST_ALIGNMENTS_QUERY_ID,
                parameters={
                    "publication_id": source_publication_id,
                    "limit": selected_page.limit,
                    "offset": selected_page.offset,
                },
            )
        )
        _require_result_row_bound(result, selected_page.limit)
        rows = _row_mappings(result, fields=ALIGNMENT_RESULT_FIELDS)
        alignments = tuple(_alignment_with_competency(row) for row in rows)
        if any(
            item.alignment.publication_id != source_publication_id for item in alignments
        ):
            raise Neo4jRepositoryResponseError()
        _reject_duplicate_identities(
            tuple(
                (
                    item.alignment.publication_id,
                    item.source_enrichment.id,
                    item.competency.id,
                    item.alignment.decision_id,
                )
                for item in alignments
            )
        )
        return alignments

    async def get_enrichment(
        self, publication_id: str, enrichment_id: str
    ) -> Neo4jEnrichment | None:
        if self.max_rows < _SELECTED_ENRICHMENT_ROW_LIMIT:
            raise Neo4jRepositoryRequestError()
        source_publication_id = _require_nonempty_source_id(publication_id)
        source_enrichment_id = _require_nonempty_source_id(enrichment_id)
        result = await self.client.execute(
            Neo4jReadQuery(
                query_id=GET_ENRICHMENT_QUERY_ID,
                parameters={
                    "publication_id": source_publication_id,
                    "enrichment_id": source_enrichment_id,
                    "limit": _SELECTED_ENRICHMENT_ROW_LIMIT,
                },
            )
        )
        _require_result_row_bound(result, _SELECTED_ENRICHMENT_ROW_LIMIT)
        enrichments = _parse_records(
            result,
            fields=ENRICHMENT_FIELDS,
            model_type=Neo4jEnrichment,
        )
        if len(enrichments) > 1:
            raise Neo4jDuplicateSourceIdentityError()
        if not enrichments:
            return None
        enrichment = enrichments[0]
        if (
            enrichment.publication_id != source_publication_id
            or enrichment.id != source_enrichment_id
        ):
            raise Neo4jRepositoryResponseError()
        return enrichment

    async def _require_publication(self, publication_id: str) -> None:
        if self.max_rows < _SELECTED_PUBLICATION_ROW_LIMIT:
            raise Neo4jRepositoryRequestError()
        result = await self.client.execute(
            Neo4jReadQuery(
                query_id=GET_PUBLICATION_BY_ID_QUERY_ID,
                parameters={
                    "publication_id": publication_id,
                    "limit": _SELECTED_PUBLICATION_ROW_LIMIT,
                },
            )
        )
        _require_result_row_bound(result, _SELECTED_PUBLICATION_ROW_LIMIT)
        publications = _parse_records(
            result,
            fields=PUBLICATION_FIELDS,
            model_type=Neo4jPublication,
        )
        match publications:
            case ():
                raise Neo4jPublicationNotFoundError()
            case (publication,):
                if publication.publication_id != publication_id:
                    raise Neo4jRepositoryResponseError()
            case _:
                raise Neo4jDuplicateSourceIdentityError()

    def _page_for(self, page: Neo4jPagination | None) -> Neo4jPagination:
        selected_page = page or Neo4jPagination(limit=self.max_rows, offset=0)
        if selected_page.limit > self.max_rows:
            raise Neo4jRepositoryRequestError()
        return selected_page
def _require_nonempty_source_id(value: str) -> str:
    if not value.strip():
        raise Neo4jRepositoryRequestError()
    return value
