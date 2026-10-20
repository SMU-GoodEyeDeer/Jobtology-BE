import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

import anyio
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from jobtology_be.application.services.roadmaps import RoadmapService
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import analyses, goals, recompute_requests
from jobtology_be.workers.recompute import LeasedRecomputeWorker

_logger = logging.getLogger(__name__)
RECOMPUTE_CYCLE_DELAY_SECONDS = 3


@dataclass(slots=True)
class WorkerHealth:
    running: bool = False
    last_success: float | None = None
    last_cycle_failed: bool = False
    last_cycle: float | None = None
    consecutive_failures: int = 0
    failure_threshold: int = 3
    stale_after_seconds: float = 90

    @property
    def ready(self) -> bool:
        return (self.running and self.last_cycle is not None
                and time.monotonic() - self.last_cycle < self.stale_after_seconds
                and self.consecutive_failures < self.failure_threshold)


class RoleNames(Protocol):
    def display_name(self, occupation_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class InProcessRecomputeLoop:
    worker: LeasedRecomputeWorker
    database: Database
    roadmap_service: RoadmapService
    holder: RoleNames
    health: WorkerHealth = field(default_factory=WorkerHealth, compare=False)

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
        deferred_error: SQLAlchemyError | None = None
        for row in pending:
            name = (
                self.holder.display_name(row.occupation_id) or row.occupation_id
                if row.occupation_id is not None else "Career"
            )
            try:
                await self.roadmap_service.reconcile_automatic(row.id, f"{name} 로드맵")
            except SQLAlchemyError as error:
                # Each request gets its own transaction; one transient failure
                # must not prevent other accounts' follow-up work from progressing.
                _logger.warning("automatic roadmap deferred after database error", exc_info=True)
                deferred_error = error
        if deferred_error is not None:
            # Continue other accounts above, but do not report this cycle as a
            # success while an automatic-roadmap transaction is still failing.
            raise deferred_error

    async def run(self) -> None:
        self.health.running = True
        try:
            while True:
                try:
                    await self.process_once()
                except Exception:
                    self.health.last_cycle_failed = True
                    self.health.consecutive_failures += 1
                    _logger.exception("product role recompute cycle deferred after error")
                else:
                    self.health.last_success = time.monotonic()
                    self.health.last_cycle_failed = False
                    self.health.consecutive_failures = 0
                self.health.last_cycle = time.monotonic()
                await anyio.sleep(RECOMPUTE_CYCLE_DELAY_SECONDS)
        finally:
            self.health.running = False
