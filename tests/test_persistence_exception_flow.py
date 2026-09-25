from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from jobtology_be.infrastructure.persistence.contracts import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    MissingRecordError,
    OwnershipError,
)


@contextmanager
def transaction_boundary() -> Iterator[None]:
    yield


@pytest.mark.parametrize(
    "error",
    [
        MissingRecordError(resource="goal"),
        OwnershipError(resource="goal"),
        IdempotencyConflictError(key="request"),
        IdempotencyInProgressError(key="request"),
    ],
)
def test_transaction_boundary_preserves_typed_exception(error: Exception) -> None:
    with pytest.raises(type(error)) as caught, transaction_boundary():
        raise error

    assert caught.value is error
