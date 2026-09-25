from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencySnapshot,
    MissingRecordError,
)
from jobtology_be.infrastructure.persistence.schema import idempotency_records


async def acquire_idempotency(
    session: AsyncSession, request: IdempotencyAcquire
) -> IdempotencySnapshot:
    await session.execute(
        delete(idempotency_records).where(
            idempotency_records.c.user_id == request.user_id,
            idempotency_records.c.method == request.method,
            idempotency_records.c.path == request.path,
            idempotency_records.c.key == request.key,
            idempotency_records.c.response_status.is_not(None),
            idempotency_records.c.expires_at <= datetime.now(UTC),
        )
    )
    inserted_request_hash = await session.scalar(
        postgres_insert(idempotency_records)
        .values(
            user_id=request.user_id,
            method=request.method,
            path=request.path,
            key=request.key,
            request_hash=request.request_hash,
            expires_at=request.expires_at,
        )
        .on_conflict_do_nothing()
        .returning(idempotency_records.c.request_hash)
    )
    if inserted_request_hash is not None:
        return IdempotencySnapshot(response_status=None, response=None)
    row = (
        await session.execute(
            select(
                idempotency_records.c.request_hash,
                idempotency_records.c.response_status,
                idempotency_records.c.response,
            )
            .where(
                idempotency_records.c.user_id == request.user_id,
                idempotency_records.c.method == request.method,
                idempotency_records.c.path == request.path,
                idempotency_records.c.key == request.key,
            )
            .with_for_update()
        )
    ).one()
    if row.request_hash != request.request_hash:
        raise IdempotencyConflictError(key=request.key)
    if row.response_status is None:
        raise IdempotencyInProgressError(key=request.key)
    return IdempotencySnapshot(response_status=row.response_status, response=row.response)


async def complete_idempotency(session: AsyncSession, request: IdempotencyComplete) -> None:
    completed_key = await session.scalar(
        update(idempotency_records)
        .where(
            idempotency_records.c.user_id == request.request.user_id,
            idempotency_records.c.method == request.request.method,
            idempotency_records.c.path == request.request.path,
            idempotency_records.c.key == request.request.key,
            idempotency_records.c.request_hash == request.request.request_hash,
        )
        .values(response_status=request.response_status, response=dict(request.response))
        .returning(idempotency_records.c.key)
    )
    if completed_key is None:
        raise MissingRecordError(resource="idempotency record")
