from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictStr,
    ValidationError,
)

from jobtology_be.corpus.neo4j_client import Neo4jQueryApiError, Neo4jQueryResult
from jobtology_be.corpus.neo4j_models import (
    Neo4jNcsAlignment,
    Neo4jNcsCompetencyNode,
)


class Neo4jRepositoryError(Neo4jQueryApiError):
    pass


class Neo4jRepositoryRequestError(Neo4jRepositoryError):
    def __init__(self) -> None:
        super().__init__("Neo4j corpus repository request is invalid")


class Neo4jRepositoryResponseError(Neo4jRepositoryError):
    def __init__(self) -> None:
        super().__init__("Neo4j corpus repository response is invalid")


class Neo4jPublicationNotFoundError(Neo4jRepositoryError):
    def __init__(self) -> None:
        super().__init__("Neo4j corpus publication is absent")


class Neo4jDuplicateSourceIdentityError(Neo4jRepositoryError):
    def __init__(self) -> None:
        super().__init__("Neo4j corpus repository returned duplicate source identities")


class Neo4jNcsAlignmentSource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    id: StrictStr = Field(min_length=1)
    posting_id: StrictStr = Field(min_length=1)
    current: StrictBool


@dataclass(frozen=True, slots=True)
class Neo4jNcsAlignmentWithCompetency:
    source_enrichment: Neo4jNcsAlignmentSource
    alignment: Neo4jNcsAlignment
    competency: Neo4jNcsCompetencyNode


def _alignment_with_competency(
    row: Mapping[str, JsonValue],
) -> Neo4jNcsAlignmentWithCompetency:
    try:
        source_enrichment = Neo4jNcsAlignmentSource.model_validate(
            {
                "id": row["source_enrichment_id"],
                "posting_id": row["source_posting_id"],
                "current": row["source_current"],
            }
        )
        alignment = Neo4jNcsAlignment.model_validate(
            {
                "accepted": row["accepted"],
                "decision_id": row["decision_id"],
                "publication_id": row["publication_id"],
            }
        )
        competency = Neo4jNcsCompetencyNode.model_validate(
            {
                "id": row["competency_id"],
                "code": row["competency_code"],
                "kind": row["competency_kind"],
                "name": row["competency_name"],
                "name_source_record_id": row["competency_name_source_record_id"],
                "name_source_run_id": row["competency_name_source_run_id"],
            }
        )
    except ValidationError:
        raise Neo4jRepositoryResponseError() from None
    return Neo4jNcsAlignmentWithCompetency(
        source_enrichment=source_enrichment,
        alignment=alignment,
        competency=competency,
    )


def _parse_records[RecordT: BaseModel](
    result: Neo4jQueryResult,
    *,
    fields: tuple[str, ...],
    model_type: type[RecordT],
) -> tuple[RecordT, ...]:
    try:
        return tuple(
            model_type.model_validate(row) for row in _row_mappings(result, fields=fields)
        )
    except ValidationError:
        raise Neo4jRepositoryResponseError() from None


def _reject_duplicate_identities[IdentityT: Hashable](identities: tuple[IdentityT, ...]) -> None:
    if len(identities) != len(frozenset(identities)):
        raise Neo4jDuplicateSourceIdentityError()


def _require_result_row_bound(result: Neo4jQueryResult, limit: int) -> None:
    if len(result.rows) > limit:
        raise Neo4jRepositoryResponseError()


def _require_response_source_id(value: JsonValue) -> str:
    match value:
        case str() as source_id if source_id.strip():
            return source_id
        case _:
            raise Neo4jRepositoryResponseError()


def _row_mappings(
    result: Neo4jQueryResult,
    *,
    fields: tuple[str, ...],
) -> tuple[Mapping[str, JsonValue], ...]:
    if result.fields != fields:
        raise Neo4jRepositoryResponseError()
    rows: list[Mapping[str, JsonValue]] = []
    for row in result.rows:
        if len(row) != len(fields):
            raise Neo4jRepositoryResponseError()
        rows.append(dict(zip(fields, row, strict=True)))
    return tuple(rows)
