import logging
from dataclasses import dataclass
from typing import Protocol

import anyio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from jobtology_be.application.services.roadmaps import RoadmapService
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import analyses, goals, recompute_requests
from jobtology_be.workers.recompute import LeasedRecomputeWorker

_logger = logging.getLogger(__name__)


class RoleNames(Protocol):
    def display_name(self, occupation_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class InProcessRecomputeLoop:
    worker: LeasedRecomputeWorker
    database: Database
    roadmap_service: RoadmapService
    holder: RoleNames

    async def process_once(self) -> None:
        await self.worker.process_once(limit=1)
        # Finalization records this follow-up in the same transaction as the
        # analysis. Re-read it every cycle so restart or a failed SQL transaction
        # cannot silently lose automatic roadmap creation.
        async with self.database.sessions() as session:
            pending = (await session.execute(
                select(recompute_requests.c.id, goals.c.occupation_id)
                .join(analyses, analyses.c.id == recompute_requests.c.resulting_analysis_id)
                .join(goals, goals.c.id == analyses.c.goal_id)
                .where(recompute_requests.c.auto_roadmap_pending.is_(True))
                .order_by(recompute_requests.c.created_at, recompute_requests.c.id)
                .limit(10)
            )).all()
        for row in pending:
            name = (
                self.holder.display_name(row.occupation_id) or row.occupation_id
                if row.occupation_id is not None else "Career"
            )
            try:
                await self.roadmap_service.reconcile_automatic(row.id, f"{name} 로드맵")
            except SQLAlchemyError:
                # Each request gets its own transaction; one transient failure
                # must not prevent other accounts' follow-up work from progressing.
                _logger.warning("automatic roadmap deferred after database error", exc_info=True)

    async def run(self) -> None:
        while True:
            try:
                await self.process_once()
            except SQLAlchemyError:
                _logger.warning("product role recompute cycle deferred after database error", exc_info=True)
            await anyio.sleep(3)
