"""Transactional account budgets for new analysis requests and solver executions.

Counters are bounded, reused fixed windows; they survive restarts and are removed
with the account. A caller must roll back the transaction/savepoint on rejection.
"""

from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.schema import account_rate_limits


class AnalysisBudgetExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Account analysis budget exceeded")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AnalysisBudgetPolicy:
    requests_per_hour: int = 10
    executions_per_hour: int = 30
    executions_per_minute: int = 2

    def __post_init__(self) -> None:
        for value in (self.requests_per_hour, self.executions_per_hour, self.executions_per_minute):
            if isinstance(value, bool) or not 1 <= value < 2_147_483_647:
                raise ValueError("Analysis budgets must be positive bounded integers")


async def reserve_budget(
    session: AsyncSession, user_id: UUID, *, scope: str, limit: int, unit: str,
) -> None:
    """Reserve exactly one unit, atomically, in the caller's transaction."""
    if unit not in {"minute", "hour"}:
        raise ValueError("Unsupported budget window")
    counter = account_rate_limits
    statement = insert(counter).values(
        user_id=user_id, scope=scope, request_count=1,
        window_started_at=func.date_trunc(unit, func.clock_timestamp()),
    )
    bucket = statement.excluded.window_started_at
    statement = statement.on_conflict_do_update(
        index_elements=[counter.c.user_id, counter.c.scope],
        set_={
            "window_started_at": func.greatest(counter.c.window_started_at, bucket),
            "request_count": case(
                (counter.c.window_started_at < bucket, 1),
                else_=counter.c.request_count + 1,
            ),
        },
        where=(counter.c.window_started_at < bucket) | (counter.c.request_count < limit),
    ).returning(counter.c.request_count)
    if await session.scalar(statement) is not None:
        return
    row = (await session.execute(select(
        counter.c.window_started_at, func.clock_timestamp().label("now"),
    ).where(counter.c.user_id == user_id, counter.c.scope == scope))).one()
    duration = timedelta(minutes=1) if unit == "minute" else timedelta(hours=1)
    raise AnalysisBudgetExceeded(max(1, ceil((row.window_started_at + duration - row.now).total_seconds())))


async def reserve_request(session: AsyncSession, user_id: UUID, policy: AnalysisBudgetPolicy) -> None:
    # Matching work and HTTP idempotency replays never reach this reservation.
    await reserve_budget(session, user_id, scope="analysis_request_hour",
                         limit=policy.requests_per_hour, unit="hour")


async def reserve_execution(session: AsyncSession, user_id: UUID, policy: AnalysisBudgetPolicy) -> None:
    # Includes automatic recomputations from edits and completed roadmap steps.
    await reserve_budget(session, user_id, scope="analysis_compute_hour",
                         limit=policy.executions_per_hour, unit="hour")
    await reserve_budget(session, user_id, scope="analysis_compute_minute",
                         limit=policy.executions_per_minute, unit="minute")
