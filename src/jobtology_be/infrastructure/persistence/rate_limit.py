"""Shared, bounded per-account chat quotas using PostgreSQL's clock and row locks."""

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import Protocol
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobtology_be.infrastructure.persistence.schema import account_rate_limits


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class RateLimitUnavailableError(Exception):
    """The account quota could not be checked safely."""


class AccountRateLimiter(Protocol):
    async def consume(self, user_id: UUID) -> RateLimitDecision: ...


class PostgresAccountRateLimiter:
    """One fixed-minute counter per Google-backed internal account, across workers.

    Logging out or signing in again retains the same account counter. The row is
    reused each minute rather than retaining historical request/session records.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, limit_per_minute: int = 20
    ) -> None:
        if not 1 <= limit_per_minute < 2_147_483_647:
            raise ValueError("limit_per_minute must be a positive bounded integer")
        self._sessions = sessions
        self._limit = limit_per_minute

    async def consume(self, user_id: UUID) -> RateLimitDecision:
        counter = account_rate_limits
        statement = insert(counter).values(
            user_id=user_id,
            scope="chat",
            window_started_at=func.date_trunc("minute", func.clock_timestamp()),
            request_count=1,
        )
        bucket = statement.excluded.window_started_at
        statement = statement.on_conflict_do_update(
            index_elements=[counter.c.user_id, counter.c.scope],
            set_={
                # An older queued request must never move the window backwards.
                "window_started_at": func.greatest(counter.c.window_started_at, bucket),
                "request_count": case(
                    (counter.c.window_started_at < bucket, 1),
                    else_=func.least(counter.c.request_count, self._limit) + 1,
                ),
            },
        ).returning(
            counter.c.request_count,
            counter.c.window_started_at,
            func.clock_timestamp().label("checked_at"),
        )
        try:
            async with self._sessions.begin() as session:
                row = (await session.execute(statement)).one()
        except (SQLAlchemyError, OSError, TimeoutError) as error:
            raise RateLimitUnavailableError("Account rate limit unavailable") from error
        retry_after = max(
            1, ceil((row.window_started_at + timedelta(minutes=1) - row.checked_at).total_seconds())
        )
        return RateLimitDecision(
            allowed=row.request_count <= self._limit,
            retry_after_seconds=retry_after,
        )
