from dataclasses import dataclass
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jobtology_be.api.source_catalog_models import (
    CatalogEntities,
    CatalogEntity,
    CatalogRelations,
    CatalogSummary,
    EntityKind,
)

type CatalogOperation = Literal["summary", "entities", "entity", "relations"]
_FUNCTIONS: Final = {
    "summary": "SELECT CAST(catalog.catalog_summary_v1(:release_id) AS text)",
    "entities": "SELECT CAST(catalog.catalog_entities_v1(:release_id, :kind, :limit, :offset) AS text)",
    "entity": "SELECT CAST(catalog.catalog_entity_v1(:release_id, :entity_id) AS text)",
    "relations": "SELECT CAST(catalog.catalog_relations_v1(:release_id, :entity_id, :limit, :offset) AS text)",
}
_ERROR_STATUS: Final = {
    "CATALOG_RELEASE_NOT_APPROVED": 503,
    "CATALOG_APPROVAL_STALE": 503,
    "CATALOG_RELEASE_UNAVAILABLE": 410,
    "CATALOG_ENTITY_NOT_IN_RELEASE": 404,
    "INVALID_CATALOG_PAGE": 422,
    "INVALID_CATALOG_ENTITY_KIND": 422,
}


@dataclass(frozen=True, slots=True)
class CatalogReadError(Exception):
    status_code: int


def catalog_error_status(error: DBAPIError) -> int:
    if error.orig is None:
        return 503
    cause = error.orig.__cause__ or error.orig
    message = getattr(cause, "message", None)
    sqlstate = getattr(cause, "sqlstate", None)
    if sqlstate == "P0001" and message in _ERROR_STATUS:
        return _ERROR_STATUS[message]
    return 503


class CatalogQueries(Protocol):
    async def summary(self, release_id: str | None) -> CatalogSummary: ...
    async def entities(
        self, release_id: str | None, kind: EntityKind | None, limit: int, offset: int
    ) -> CatalogEntities: ...
    async def entity(self, release_id: str | None, entity_id: str) -> CatalogEntity: ...
    async def relations(
        self, release_id: str | None, entity_id: str, limit: int, offset: int
    ) -> CatalogRelations: ...


@dataclass(frozen=True, slots=True)
class PostgresSourceCatalog:
    engine: AsyncEngine

    @classmethod
    def create(cls, database_url: str) -> "PostgresSourceCatalog":
        return cls(create_async_engine(
            database_url, pool_size=4, max_overflow=0, pool_timeout=5,
            pool_pre_ping=True, connect_args={"timeout": 5, "command_timeout": 5},
            hide_parameters=True,
        ))

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def _read[
        T: BaseModel
    ](self, operation: CatalogOperation, params: dict[str, str | int | None], model: type[T]) -> T:
        try:
            async with self.engine.connect() as connection, connection.begin():
                _ = await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                result = await connection.execute(text(_FUNCTIONS[operation]), params)
                payload = result.scalar_one()
            if not isinstance(payload, str):
                raise CatalogReadError(503)
            return model.model_validate_json(payload)
        except DBAPIError as error:
            raise CatalogReadError(catalog_error_status(error)) from None
        except (SQLAlchemyError, ValidationError, ValueError, TypeError, TimeoutError, OSError):
            raise CatalogReadError(503) from None

    async def summary(self, release_id: str | None) -> CatalogSummary:
        return await self._read("summary", {"release_id": release_id}, CatalogSummary)

    async def entities(
        self, release_id: str | None, kind: EntityKind | None, limit: int, offset: int
    ) -> CatalogEntities:
        return await self._read("entities", {
            "release_id": release_id, "kind": kind, "limit": limit, "offset": offset,
        }, CatalogEntities)

    async def entity(self, release_id: str | None, entity_id: str) -> CatalogEntity:
        return await self._read("entity", {
            "release_id": release_id, "entity_id": entity_id,
        }, CatalogEntity)

    async def relations(
        self, release_id: str | None, entity_id: str, limit: int, offset: int
    ) -> CatalogRelations:
        return await self._read("relations", {
            "release_id": release_id, "entity_id": entity_id, "limit": limit, "offset": offset,
        }, CatalogRelations)
