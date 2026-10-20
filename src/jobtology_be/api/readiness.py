"""Runtime readiness, independent of process liveness."""
from dataclasses import dataclass

import anyio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.product_roles.holder import ProductRoleHolder
from jobtology_be.product_roles.worker import InProcessRecomputeLoop


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    database: Database | None
    role_holder: ProductRoleHolder | None
    worker: InProcessRecomputeLoop | None
    role_catalog_required: bool = False
    worker_required: bool = False

    async def ready(self) -> bool:
        if self.role_catalog_required and self.role_holder is None:
            return False
        if self.worker_required and self.worker is None:
            return False
        if self.role_holder is not None and not self.role_holder.available:
            return False
        if self.worker is not None and not self.worker.health.ready:
            return False
        if self.database is not None:
            try:
                with anyio.fail_after(2):
                    async with self.database.sessions() as session:
                        await session.execute(text("SELECT 1"))
            except (SQLAlchemyError, TimeoutError, OSError):
                return False
        return True
