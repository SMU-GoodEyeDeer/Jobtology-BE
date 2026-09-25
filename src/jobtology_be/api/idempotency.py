import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel

from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyAcquire,
    IdempotencyComplete,
    IdempotencySnapshot,
    JsonValue,
)

IDEMPOTENCY_TTL: Final = timedelta(hours=24)

class IdempotencyStore(Protocol):
    async def acquire_idempotency(self, request: IdempotencyAcquire) -> IdempotencySnapshot: ...

    async def complete_idempotency(self, request: IdempotencyComplete) -> None: ...

    async def execute_idempotency(
        self,
        request: IdempotencyAcquire,
        response_status: int,
        operation: Callable[[], Awaitable[Mapping[str, JsonValue]]],
    ) -> IdempotencySnapshot: ...


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    store: IdempotencyStore
    request: IdempotencyAcquire


@dataclass(frozen=True, slots=True)
class IdempotencyRequest:
    user_id: UUID
    method: str
    path: str
    key: str
    payload: BaseModel


@dataclass(frozen=True, slots=True)
class IdempotencyReplay:
    response_status: int
    response: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class IdempotencyCompletion:
    reservation: IdempotencyReservation
    response_status: int
    response: BaseModel


async def require_idempotency_store() -> IdempotencyStore | None:
    return None


def _request_hash(payload: BaseModel) -> str:
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _acquire_request(command: IdempotencyRequest) -> IdempotencyAcquire:
    return IdempotencyAcquire(
        user_id=command.user_id,
        method=command.method,
        path=command.path,
        key=command.key,
        request_hash=_request_hash(command.payload),
        expires_at=datetime.now(UTC) + IDEMPOTENCY_TTL,
    )


async def reserve(
    store: IdempotencyStore,
    command: IdempotencyRequest,
) -> IdempotencyReservation | IdempotencyReplay:
    request = _acquire_request(command)
    snapshot = await store.acquire_idempotency(request)
    if snapshot.response_status is None:
        return IdempotencyReservation(store=store, request=request)
    if snapshot.response is None:
        raise HTTPException(status_code=503)
    return IdempotencyReplay(
        response_status=snapshot.response_status,
        response=snapshot.response,
    )


async def reserve_if_requested(
    store: IdempotencyStore | None,
    command: IdempotencyRequest | None,
) -> IdempotencyReservation | IdempotencyReplay | None:
    if command is None:
        return None
    if store is None:
        raise HTTPException(status_code=503)
    return await reserve(store, command)


async def execute_if_requested[ResponseT: BaseModel](
    store: IdempotencyStore | None,
    command: IdempotencyRequest | None,
    response_status: int,
    operation: Callable[[], Awaitable[ResponseT]],
) -> ResponseT | IdempotencyReplay:
    if command is None:
        return await operation()
    if store is None:
        raise HTTPException(status_code=503)

    async def serialized_operation() -> Mapping[str, JsonValue]:
        return _response_body(await operation())

    snapshot = await store.execute_idempotency(
        _acquire_request(command), response_status, serialized_operation
    )
    if snapshot.response_status is None or snapshot.response is None:
        raise HTTPException(status_code=503)
    return IdempotencyReplay(
        response_status=snapshot.response_status,
        response=snapshot.response,
    )


async def complete(
    command: IdempotencyCompletion,
) -> None:
    await command.reservation.store.complete_idempotency(
        IdempotencyComplete(
            request=command.reservation.request,
            response_status=command.response_status,
            response=_response_body(command.response),
        )
    )


def _response_body(response: BaseModel) -> Mapping[str, JsonValue]:
    return response.model_dump(mode="json")
