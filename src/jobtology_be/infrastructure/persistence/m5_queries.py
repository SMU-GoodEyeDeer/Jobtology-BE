from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from jobtology_be.application.m5_queries import (
    M5DataUnavailableError,
    OccupationView,
    RouteProposalView,
    TraceView,
)
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.infrastructure.persistence.contracts import MissingRecordError
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.m5_dashboard_queries import PostgresM5DashboardQueries
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    calculation_traces,
    route_proposals,
)
from jobtology_be.modules.analyses.editorial_models import ReleaseState


def _current_utc() -> datetime:
    return datetime.now(UTC)


class PostgresM5Queries(PostgresM5DashboardQueries):
    def __init__(
        self,
        database: Database,
        snapshot_reader: LocalJsonPublishedCorpusSnapshotReader | None = None,
        now: Callable[[], datetime] = _current_utc,
    ) -> None:
        super().__init__(database, now)
        self._snapshot_reader = snapshot_reader

    async def get_occupations(self) -> tuple[OccupationView, ...]:
        if self._snapshot_reader is None:
            raise M5DataUnavailableError(resource="occupation catalog")
        return tuple(
            OccupationView(
                occupation_id=snapshot.occupation_id,
                basis_version=snapshot.basis_version,
                release_id=snapshot.release.release_id,
                reviewed_at=snapshot.release.reviewed_at,
            )
            for snapshot in self._snapshot_reader.snapshots
            if snapshot.release.state is ReleaseState.PUBLISHED
            and not snapshot.is_fixture
            and snapshot.release.release_id is not None
        )

    async def get_route_proposal(self, user_id: UUID, proposal_id: UUID) -> RouteProposalView:
        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            route_proposals.c.id,
                            route_proposals.c.analysis_id,
                            route_proposals.c.decision_trace_id,
                            route_proposals.c.proposal_hash,
                            route_proposals.c.profile_version,
                            route_proposals.c.feasibility,
                            route_proposals.c.optimization_status,
                            route_proposals.c.constraints_snapshot,
                            route_proposals.c.steps,
                            route_proposals.c.created_at,
                            analyses.c.basis_version,
                            analyses.c.release_id,
                            analyses.c.methodology_version,
                        )
                        .join(analyses, analyses.c.id == route_proposals.c.analysis_id)
                        .where(route_proposals.c.id == proposal_id, route_proposals.c.user_id == user_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise MissingRecordError(resource="route proposal")
        return RouteProposalView(
            proposal_id=row["id"],
            analysis_id=row["analysis_id"],
            decision_trace_id=row["decision_trace_id"],
            proposal_hash=row["proposal_hash"],
            profile_version=row["profile_version"],
            feasibility=row["feasibility"],
            optimization_status=row["optimization_status"],
            constraints_snapshot=row["constraints_snapshot"],
            steps=tuple(row["steps"]),
            created_at=row["created_at"],
            basis_version=row["basis_version"],
            release_id=row["release_id"],
            methodology_version=row["methodology_version"],
        )

    async def get_trace(self, user_id: UUID, trace_id: UUID) -> TraceView:
        async with self._database.sessions() as session:
            row = (
                (
                    await session.execute(
                        select(
                            calculation_traces.c.id,
                            calculation_traces.c.kind,
                            calculation_traces.c.input_hash,
                            calculation_traces.c.versions,
                            calculation_traces.c.release_id,
                            calculation_traces.c.outputs,
                            calculation_traces.c.created_at,
                        ).where(
                            calculation_traces.c.id == trace_id,
                            calculation_traces.c.user_id == user_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise MissingRecordError(resource="trace")
        return TraceView(
            trace_id=row["id"],
            kind=row["kind"],
            input_hash=row["input_hash"],
            versions=row["versions"],
            release_id=row["release_id"],
            outputs=row["outputs"],
            created_at=row["created_at"],
        )
