from datetime import timedelta
from typing import Any, Final, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import CursorResult

from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    OutboxLease,
    PersistenceConflictError,
    RecomputeRequestCreate,
    RecomputeRequestSnapshot,
    RecomputeWorkItem,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    outbox_jobs,
    recompute_contexts,
    recompute_requests,
)
from jobtology_be.settings import CorpusSource

MISSING_RECOMPUTE_CONTEXT: Final = "MISSING_RECOMPUTE_CONTEXT"
RECOMPUTE_LEASE_FOR: Final = timedelta(minutes=5)


class OutboxRepository:
    _database: Database

    def __init__(self, database: Database) -> None:
        self._database = database

    async def _enqueue_recompute(
        self, session, user_id: UUID, profile_version: int, event_id: UUID
    ) -> None:
        await self._persist_recompute_request(
            session,
            RecomputeRequestCreate(
                user_id=user_id,
                profile_version=profile_version,
                trigger_event_id=event_id,
                dedupe_key=f"missing-context:{event_id}",
                payload={},
            ),
        )

    async def _persist_recompute_request(
        self, session, request: RecomputeRequestCreate
    ) -> RecomputeRequestSnapshot:
        request_id = uuid4()
        state = "PENDING" if request.context is not None else "FAILED"
        error_code = None if request.context is not None else MISSING_RECOMPUTE_CONTEXT
        await session.execute(
            postgres_insert(recompute_requests)
            .values(
                id=request_id,
                user_id=request.user_id,
                profile_version=request.profile_version,
                trigger_event_id=request.trigger_event_id,
                state=state,
                error_code=error_code,
            )
            .on_conflict_do_nothing()
        )
        persisted_request = await session.execute(
            select(recompute_requests.c.id, recompute_requests.c.state).where(
                recompute_requests.c.user_id == request.user_id,
                recompute_requests.c.profile_version == request.profile_version,
                recompute_requests.c.trigger_event_id == request.trigger_event_id,
            )
        )
        row = persisted_request.one_or_none()
        if row is None:
            raise MissingRecordError(resource="recompute request")
        if request.context is not None and row.state == "PENDING":
            await session.execute(
                postgres_insert(recompute_contexts)
                .values(request_id=row.id, payload=dict(request.context))
                .on_conflict_do_nothing()
            )
            outbox_request_id = await session.scalar(
                postgres_insert(outbox_jobs)
                .values(
                    id=uuid4(),
                    kind="RECOMPUTE",
                    payload={**dict(request.payload), "recompute_request_id": str(row.id)},
                    recompute_request_id=row.id,
                    dedupe_key=request.dedupe_key,
                )
                .on_conflict_do_nothing()
                .returning(outbox_jobs.c.recompute_request_id)
            )
            if outbox_request_id is None:
                existing_request_id = await session.scalar(
                    select(outbox_jobs.c.recompute_request_id).where(
                        outbox_jobs.c.dedupe_key == request.dedupe_key
                    )
                )
                if existing_request_id != row.id:
                    raise PersistenceConflictError(resource="recompute dedupe")
        return RecomputeRequestSnapshot(request_id=row.id, state=row.state)

    async def claim_outbox_jobs(self, limit: int, lease_for: timedelta) -> tuple[OutboxLease, ...]:
        leases: list[OutboxLease] = []
        async with self._database.sessions.begin() as session:
            rows = (
                (
                    await session.execute(
                        select(outbox_jobs)
                        .where(
                            outbox_jobs.c.kind != "RECOMPUTE",
                            or_(
                                (outbox_jobs.c.state == "READY")
                                & (outbox_jobs.c.available_at <= func.clock_timestamp()),
                                (outbox_jobs.c.state == "LEASED")
                                & (outbox_jobs.c.lease_until < func.clock_timestamp()),
                            )
                        )
                        .order_by(outbox_jobs.c.available_at, outbox_jobs.c.id)
                        .with_for_update(skip_locked=True)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                token = uuid4()
                await session.execute(
                    update(outbox_jobs)
                    .where(outbox_jobs.c.id == row["id"])
                    .values(
                        state="LEASED",
                        lease_token=token,
                        lease_until=func.clock_timestamp() + lease_for,
                        attempt_count=outbox_jobs.c.attempt_count + 1,
                    )
                )
                leases.append(
                    OutboxLease(
                        job_id=row["id"],
                        lease_token=token,
                        kind=row["kind"],
                        payload=row["payload"],
                    )
                )
        return tuple(leases)

    async def claim_recompute_work(
        self,
        limit: int,
        *,
        eligible_corpus_sources: frozenset[CorpusSource] | None = None,
    ) -> tuple[RecomputeWorkItem, ...]:
        work_items: list[RecomputeWorkItem] = []
        async with self._database.sessions.begin() as session:
            claim_conditions = (
                outbox_jobs.c.kind == "RECOMPUTE",
                recompute_requests.c.state.in_(("PENDING", "RUNNING")),
                or_(
                    (outbox_jobs.c.state == "READY")
                    & (outbox_jobs.c.available_at <= func.clock_timestamp()),
                    (outbox_jobs.c.state == "LEASED")
                    & (outbox_jobs.c.lease_until < func.clock_timestamp()),
                ),
            )
            if eligible_corpus_sources is not None:
                claim_conditions += (
                    recompute_contexts.c.payload["snapshot_selection"]["source"]
                    .as_string()
                    .in_(eligible_corpus_sources),
                )
            rows = (
                (
                    await session.execute(
                        select(
                            outbox_jobs.c.id.label("job_id"),
                            outbox_jobs.c.kind,
                            outbox_jobs.c.payload,
                            recompute_requests.c.id.label("request_id"),
                            recompute_requests.c.user_id,
                            recompute_requests.c.profile_version,
                        )
                        .select_from(
                            outbox_jobs.join(
                                recompute_requests,
                                outbox_jobs.c.recompute_request_id == recompute_requests.c.id,
                            ).join(
                                recompute_contexts,
                                recompute_contexts.c.request_id == recompute_requests.c.id,
                            )
                        )
                        .where(*claim_conditions)
                        .order_by(outbox_jobs.c.available_at, outbox_jobs.c.id)
                        .with_for_update(of=outbox_jobs, skip_locked=True)
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                lease_token = uuid4()
                leased_job = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(outbox_jobs)
                        .where(
                            outbox_jobs.c.id == row["job_id"],
                            outbox_jobs.c.recompute_request_id == row["request_id"],
                            outbox_jobs.c.kind == "RECOMPUTE",
                        )
                        .values(
                            state="LEASED",
                            lease_token=lease_token,
                            lease_until=func.clock_timestamp() + RECOMPUTE_LEASE_FOR,
                            attempt_count=outbox_jobs.c.attempt_count + 1,
                        )
                    ),
                )
                if leased_job.rowcount != 1:
                    raise PersistenceConflictError(resource="outbox lease")
                claimed_request = cast(
                    CursorResult[Any],
                    await session.execute(
                        update(recompute_requests)
                        .where(
                            recompute_requests.c.id == row["request_id"],
                            recompute_requests.c.user_id == row["user_id"],
                            recompute_requests.c.profile_version == row["profile_version"],
                            recompute_requests.c.state.in_(("PENDING", "RUNNING")),
                        )
                        .values(state="RUNNING", updated_at=func.clock_timestamp())
                    ),
                )
                if claimed_request.rowcount != 1:
                    raise PersistenceConflictError(resource="recompute request")
                work_items.append(
                    RecomputeWorkItem(
                        request_id=row["request_id"],
                        user_id=row["user_id"],
                        profile_version=row["profile_version"],
                        lease=OutboxLease(
                            job_id=row["job_id"],
                            lease_token=lease_token,
                            kind=row["kind"],
                            payload=row["payload"],
                        ),
                    )
                )
        return tuple(work_items)

    async def complete_outbox_job(self, lease: OutboxLease) -> None:
        async with self._database.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(outbox_jobs)
                    .where(
                        outbox_jobs.c.id == lease.job_id,
                        outbox_jobs.c.kind != "RECOMPUTE",
                        outbox_jobs.c.lease_token == lease.lease_token,
                        outbox_jobs.c.state == "LEASED",
                        outbox_jobs.c.lease_until > func.clock_timestamp(),
                    )
                    .values(state="COMPLETED", lease_token=None, lease_until=None)
                ),
            )
            if result.rowcount != 1:
                raise PersistenceConflictError(resource="outbox lease")
