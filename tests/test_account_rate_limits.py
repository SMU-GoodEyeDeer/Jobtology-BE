from collections.abc import Sequence
from datetime import timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jobtology_be.api.chat import require_chat_rate_limiter, require_chat_service, router
from jobtology_be.api.identity import AuthenticatedPrincipal, require_authenticated_principal
from jobtology_be.chat.service import ChatReply
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.rate_limit import (
    PostgresAccountRateLimiter,
    RateLimitDecision,
    RateLimitUnavailableError,
)
from jobtology_be.infrastructure.persistence.schema import account_rate_limits, users
from jobtology_be.llm.client import LlmMessage

pytest_plugins = ("test_acceptance_persistence",)

USER_ID = UUID("f3f6dd14-e30e-4234-9c4b-51b1b97fce2b")


class StubLimiter:
    def __init__(self, *, allowed: bool, unavailable: bool = False) -> None:
        self.allowed = allowed
        self.unavailable = unavailable
        self.accounts: list[UUID] = []

    async def consume(self, user_id: UUID) -> RateLimitDecision:
        self.accounts.append(user_id)
        if self.unavailable:
            raise RateLimitUnavailableError
        return RateLimitDecision(allowed=self.allowed, retry_after_seconds=17)


class StubChatService:
    def __init__(self) -> None:
        self.calls = 0

    async def respond(
        self, user_id: UUID, messages: Sequence[LlmMessage], mode: str
    ) -> ChatReply:
        self.calls += 1
        return ChatReply(reply="hello", occupation_id=None, candidates=())


@pytest.mark.parametrize(
    ("limiter", "status_code", "service_calls"),
    [
        (StubLimiter(allowed=True), 200, 1),
        (StubLimiter(allowed=False), 429, 0),
        (StubLimiter(allowed=False, unavailable=True), 503, 0),
        (None, 503, 0),
    ],
)
def test_chat_enforces_account_quota_before_calling_the_llm(
    limiter: StubLimiter | None, status_code: int, service_calls: int
) -> None:
    service = StubChatService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_authenticated_principal] = lambda: AuthenticatedPrincipal(
        user_id=USER_ID
    )
    app.dependency_overrides[require_chat_service] = lambda: service
    app.dependency_overrides[require_chat_rate_limiter] = lambda: limiter

    with TestClient(app) as client:
        response = client.post("/chat/messages", json={"messages": [{"role": "user", "text": "hi"}]})

    assert response.status_code == status_code
    assert service.calls == service_calls
    if limiter is not None:
        assert limiter.accounts == [USER_ID]
    if status_code == 429:
        assert response.headers["Retry-After"] == "17"


def test_database_failure_prevents_consuming_unchecked_requests() -> None:
    sessions = MagicMock(spec=async_sessionmaker[AsyncSession])
    sessions.begin.side_effect = SQLAlchemyError("database unavailable")
    limiter = PostgresAccountRateLimiter(sessions)

    with pytest.raises(RateLimitUnavailableError):
        anyio.run(limiter.consume, USER_ID)


async def _exercise_shared_account_quota(database_url: str) -> None:
    database = Database.create(database_url)
    user_id, another_user = uuid4(), uuid4()
    try:
        async with database.sessions.begin() as session:
            await session.execute(insert(users), [{"id": user_id}, {"id": another_user}])
            # Keep the race in one window even if the test crosses a minute boundary.
            await session.execute(insert(account_rate_limits).values(
                user_id=user_id,
                scope="chat",
                window_started_at=func.date_trunc("minute", func.clock_timestamp())
                + timedelta(minutes=1),
                request_count=1,
            ))
        first_worker = PostgresAccountRateLimiter(database.sessions, limit_per_minute=3)
        second_worker = PostgresAccountRateLimiter(database.sessions, limit_per_minute=3)
        outcomes: list[RateLimitDecision] = []

        async def consume(limiter: PostgresAccountRateLimiter) -> None:
            outcomes.append(await limiter.consume(user_id))

        async with anyio.create_task_group() as tasks:
            for index in range(12):
                tasks.start_soon(consume, first_worker if index % 2 else second_worker)

        assert sum(outcome.allowed for outcome in outcomes) == 2
        assert all(outcome.retry_after_seconds >= 1 for outcome in outcomes)
        # A new instance (restart/session change) shares the exhausted quota.
        assert not (await PostgresAccountRateLimiter(
            database.sessions, limit_per_minute=3
        ).consume(user_id)).allowed
        assert (await second_worker.consume(another_user)).allowed
        async with database.sessions() as session:
            assert await session.scalar(select(account_rate_limits.c.request_count).where(
                account_rate_limits.c.user_id == user_id
            )) == 4
            assert await session.scalar(select(func.count()).select_from(account_rate_limits)) == 2

        async with database.sessions.begin() as session:
            await session.execute(update(account_rate_limits).values(
                window_started_at=func.clock_timestamp() - timedelta(minutes=2)
            ))
        assert (await first_worker.consume(user_id)).allowed
        async with database.sessions.begin() as session:
            assert await session.scalar(select(account_rate_limits.c.request_count).where(
                account_rate_limits.c.user_id == user_id
            )) == 1
            await session.execute(delete(users).where(users.c.id == user_id))
            assert await session.scalar(select(func.count()).select_from(account_rate_limits)) == 1
    finally:
        await database.dispose()


def test_account_quota_is_atomic_shared_bounded_and_resets_on_postgres(
    acceptance_database_url: str,
) -> None:
    anyio.run(_exercise_shared_account_quota, acceptance_database_url)
