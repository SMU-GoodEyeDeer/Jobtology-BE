from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from jobtology_be.api.live_source_models import (
    ExamSessionsResponse,
    PostingResponse,
    PostingsResponse,
)

type LiveOperation = Literal["postings", "posting", "exam_sessions"]
_FUNCTIONS: Final = {
    "postings": "SELECT CAST(catalog.live_postings_v1(:q, :ncs_category, :region, CAST(:open_on AS date), :limit, :offset) AS text)",
    "posting": "SELECT CAST(catalog.live_posting_v1(:posting_id) AS text)",
    "exam_sessions": "SELECT CAST(catalog.live_exam_sessions_v1(:qualification, CAST(:from_date AS date), CAST(:to_date AS date), :limit, :offset) AS text)",
}
_ERROR_STATUS: Final = {
    "LIVE_SOURCE_UNAVAILABLE": 503,
    "INVALID_LIVE_PAGE": 422,
    "INVALID_LIVE_FILTER": 422,
    "LIVE_POSTING_NOT_FOUND": 404,
}


@dataclass(frozen=True, slots=True)
class LiveSourceReadError(Exception):
    status_code: int


def live_source_error_status(error: DBAPIError) -> int:
    if error.orig is None:
        return 503
    cause = error.orig.__cause__ or error.orig
    message = getattr(cause, "message", None)
    sqlstate = getattr(cause, "sqlstate", None)
    if sqlstate == "P0001" and message in _ERROR_STATUS:
        return _ERROR_STATUS[message]
    return 503


class LiveSourceFeed(Protocol):
    async def postings(
        self, q: str | None, ncs_category: str | None, region: str | None,
        open_on: date | None, limit: int, offset: int,
    ) -> PostingsResponse: ...

    async def posting(self, posting_id: str) -> PostingResponse: ...

    async def exam_sessions(
        self, qualification: str | None, from_date: date | None,
        to_date: date | None, limit: int, offset: int,
    ) -> ExamSessionsResponse: ...


@dataclass(frozen=True, slots=True)
class PostgresLiveSourceFeed:
    engine: AsyncEngine

    async def _read[T: BaseModel](
        self, operation: LiveOperation, params: dict[str, str | int | date | None], model: type[T],
    ) -> T:
        try:
            async with self.engine.connect() as connection, connection.begin():
                _ = await connection.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"))
                result = await connection.execute(text(_FUNCTIONS[operation]), params)
                payload = result.scalar_one()
            if not isinstance(payload, str):
                raise LiveSourceReadError(503)
            return model.model_validate_json(payload)
        except DBAPIError as error:
            raise LiveSourceReadError(live_source_error_status(error)) from None
        except (SQLAlchemyError, ValidationError, ValueError, TypeError, TimeoutError, OSError):
            raise LiveSourceReadError(503) from None

    async def postings(
        self, q: str | None, ncs_category: str | None, region: str | None,
        open_on: date | None, limit: int, offset: int,
    ) -> PostingsResponse:
        return await self._read("postings", {
            "q": q, "ncs_category": ncs_category, "region": region,
            "open_on": open_on,
            "limit": limit, "offset": offset,
        }, PostingsResponse)

    async def posting(self, posting_id: str) -> PostingResponse:
        return await self._read("posting", {"posting_id": posting_id}, PostingResponse)

    async def exam_sessions(
        self, qualification: str | None, from_date: date | None,
        to_date: date | None, limit: int, offset: int,
    ) -> ExamSessionsResponse:
        return await self._read("exam_sessions", {
            "qualification": qualification,
            "from_date": from_date,
            "to_date": to_date,
            "limit": limit, "offset": offset,
        }, ExamSessionsResponse)
