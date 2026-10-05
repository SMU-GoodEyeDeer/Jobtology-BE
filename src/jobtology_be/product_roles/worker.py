import logging
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol
from uuid import UUID

import anyio
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from jobtology_be.application.services.roadmaps import (
    RoadmapCreateCommand,
    RoadmapMutationCommand,
    RoadmapService,
)
from jobtology_be.infrastructure.persistence.contracts import (
    MissingRecordError,
    PersistenceConflictError,
    RecomputeFinalization,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    goals,
    profiles,
    recompute_requests,
    roadmaps,
    route_proposals,
)
from jobtology_be.workers.recompute import LeasedRecomputeWorker

_logger = logging.getLogger(__name__)


class RoleNames(Protocol):
    def display_name(self, occupation_id: str) -> str | None: ...


class _RoadmapCandidate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True)

    user_id: UUID
    profile_version: int
    goal_id: UUID
    feasibility: Literal["FEASIBLE", "RISKY", "PARTIAL", "INFEASIBLE"]
    occupation_id: str | None


@dataclass(frozen=True, slots=True)
class InProcessRecomputeLoop:
    worker: LeasedRecomputeWorker
    database: Database
    roadmap_service: RoadmapService
    holder: RoleNames

    async def process_once(self) -> None:
        for finalization in await self.worker.process_once(limit=1):
            await self._auto_roadmap(finalization)

    async def run(self) -> None:
        while True:
            try:
                await self.process_once()
            except SQLAlchemyError:
                _logger.warning("product role recompute cycle deferred after database error", exc_info=True)
            await anyio.sleep(3)

    async def _auto_roadmap(self, finalization: RecomputeFinalization) -> None:
        if finalization.proposal_id is None:
            return
        async with self.database.sessions() as session:
            result = await session.execute(
                select(
                    recompute_requests.c.user_id,
                    recompute_requests.c.profile_version,
                    analyses.c.goal_id,
                    route_proposals.c.feasibility,
                    goals.c.occupation_id,
                )
                .select_from(recompute_requests)
                .join(analyses, analyses.c.id == recompute_requests.c.resulting_analysis_id)
                .join(route_proposals, route_proposals.c.id == recompute_requests.c.proposal_id)
                .join(goals, goals.c.id == analyses.c.goal_id)
                .join(profiles, profiles.c.user_id == recompute_requests.c.user_id)
                .where(
                    recompute_requests.c.id == finalization.request_id,
                    analyses.c.id == finalization.analysis_id,
                    route_proposals.c.id == finalization.proposal_id,
                    recompute_requests.c.state == "READY",
                    profiles.c.version == recompute_requests.c.profile_version,
                    goals.c.goal_mode == "TARGETED",
                    goals.c.status == "ACTIVE",
                )
            )
            raw_row = result.mappings().one_or_none()
            if raw_row is None:
                return
            row = _RoadmapCandidate.model_validate(dict(raw_row))
            if row.feasibility not in {"FEASIBLE", "RISKY", "PARTIAL"} or row.occupation_id is None:
                return
            active = await session.scalar(
                select(roadmaps.c.id).where(
                    roadmaps.c.user_id == row.user_id,
                    roadmaps.c.goal_id == row.goal_id,
                    roadmaps.c.state == "ACTIVE",
                ).limit(1)
            )
            if active is not None:
                return
        name = self.holder.display_name(row.occupation_id) or row.occupation_id
        try:
            created = await self.roadmap_service.create(
                row.user_id,
                RoadmapCreateCommand(
                    expected_profile_version=row.profile_version,
                    goal_id=row.goal_id,
                    proposal_id=finalization.proposal_id,
                    title=f"{name} 로드맵",
                ),
            )
            _ = await self.roadmap_service.mutate(
                row.user_id,
                RoadmapMutationCommand(
                    roadmap_id=created.roadmap_id,
                    expected_roadmap_version=created.roadmap_version,
                    expected_profile_version=row.profile_version,
                    state="ACTIVE",
                    title=None,
                ),
            )
        except (PersistenceConflictError, MissingRecordError, IntegrityError):
            return
