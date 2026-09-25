from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from jobtology_be.corpus.neo4j_models import (
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
    Neo4jOccupationNode,
    Neo4jPublication,
)
from jobtology_be.corpus.neo4j_repository import Neo4jNcsAlignmentSource
from jobtology_be.corpus.source_availability import SourceCapabilities


class _PublicSourceRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )


class _PublicCatalogFields(_PublicSourceRecord):
    id: StrictStr = Field(min_length=1)
    code: StrictStr = Field(min_length=1)
    kind: StrictStr = Field(min_length=1)
    name: StrictStr = Field(min_length=1)


class Neo4jOccupationResponse(_PublicCatalogFields):
    @classmethod
    def from_source(cls, node: Neo4jOccupationNode) -> "Neo4jOccupationResponse":
        return cls(id=node.id, code=node.code, kind=node.kind, name=node.name)


class Neo4jNcsCompetencyResponse(_PublicCatalogFields):
    @classmethod
    def from_source(cls, node: Neo4jNcsCompetencyNode) -> "Neo4jNcsCompetencyResponse":
        return cls(id=node.id, code=node.code, kind=node.kind, name=node.name)


class Neo4jNcsAlignmentResponse(_PublicSourceRecord):
    publication_id: StrictStr = Field(min_length=1)
    source_enrichment_id: StrictStr = Field(min_length=1)
    source_posting_id: StrictStr = Field(min_length=1)
    source_current: StrictBool
    accepted: StrictBool
    competency: Neo4jNcsCompetencyResponse

    @classmethod
    def from_source(
        cls,
        source_enrichment: Neo4jNcsAlignmentSource,
        alignment: Neo4jNcsAlignment,
        competency: Neo4jNcsCompetencyNode,
    ) -> "Neo4jNcsAlignmentResponse":
        return cls(
            publication_id=alignment.publication_id,
            source_enrichment_id=source_enrichment.id,
            source_posting_id=source_enrichment.posting_id,
            source_current=source_enrichment.current,
            accepted=alignment.accepted,
            competency=Neo4jNcsCompetencyResponse.from_source(competency),
        )


class Neo4jPublicationResponse(_PublicSourceRecord):
    publication_id: StrictStr = Field(min_length=1)
    source_state: StrictStr = Field(min_length=1)
    capabilities: SourceCapabilities

    @classmethod
    def from_source(
        cls, publication: Neo4jPublication, capabilities: SourceCapabilities
    ) -> "Neo4jPublicationResponse":
        return cls(
            publication_id=publication.publication_id,
            source_state=publication.state,
            capabilities=capabilities,
        )
