"""Periodic retention for request credentials and completed replay records."""
import logging

import anyio
from sqlalchemy.exc import SQLAlchemyError

from jobtology_be.infrastructure.persistence.auth_store import prune_auth_records
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.idempotency import prune_idempotency_records

_logger = logging.getLogger(__name__)


async def cleanup_loop(database: Database) -> None:
    while True:
        await anyio.sleep(300)
        try:
            async with database.sessions.begin() as session:
                await prune_auth_records(session)
                await prune_idempotency_records(session)
        except (SQLAlchemyError, TimeoutError, OSError):
            _logger.warning("request retention cleanup deferred after database error", exc_info=True)
