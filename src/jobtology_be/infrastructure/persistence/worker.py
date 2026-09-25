from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update

from jobtology_be.infrastructure.persistence.contracts import (
    JsonValue,
    MissingRecordError,
    PersistenceConflictError,
    RecomputeArtifacts,
    RecomputeFinalization,
    RecomputeWorkItem,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    analyses,
    calculation_traces,
    goals,
    outbox_jobs,
    profiles,
    recompute_contexts,
    recompute_requests,
    route_proposals,
)


class WorkerRepository:
    _database: Database

    async def load_recompute_context(
        self, work_item: RecomputeWorkItem
    ) -> Mapping[str, JsonValue]:
        async with self._database.sessions() as session:
            context = await session.execute(
                select(recompute_contexts.c.payload)
                .select_from(
                    outbox_jobs.join(
                        recompute_requests,
                        outbox_jobs.c.recompute_request_id == recompute_requests.c.id,
                    ).join(
                        recompute_contexts,
                        recompute_contexts.c.request_id == recompute_requests.c.id,
                    )
                )
                .where(
                    outbox_jobs.c.id == work_item.lease.job_id,
                    outbox_jobs.c.kind == "RECOMPUTE",
                    outbox_jobs.c.recompute_request_id == work_item.request_id,
                    outbox_jobs.c.state == "LEASED",
                    outbox_jobs.c.lease_token == work_item.lease.lease_token,
                    outbox_jobs.c.lease_until > func.clock_timestamp(),
                    recompute_requests.c.user_id == work_item.user_id,
                    recompute_requests.c.profile_version == work_item.profile_version,
                    recompute_requests.c.state == "RUNNING",
                )
            )
            payload = context.scalar_one_or_none()
        if payload is None:
            raise PersistenceConflictError(resource="recompute context")
        return dict(payload)

    async def finalize_recompute(
        self, work_item: RecomputeWorkItem, artifacts: RecomputeArtifacts
    ) -> RecomputeFinalization:
        analysis = artifacts.analysis
        if (
            analysis.user_id != work_item.user_id
            or analysis.profile_version != work_item.profile_version
        ):
            raise PersistenceConflictError(resource="analysis work item")
        proposal = artifacts.proposal
        if proposal is not None and (
            proposal.user_id != work_item.user_id
            or proposal.profile_version != work_item.profile_version
            or proposal.analysis_id != analysis.analysis_id
        ):
            raise PersistenceConflictError(resource="route proposal work item")
        now = datetime.now(UTC)
        async with self._database.sessions.begin() as session:
            lease = await session.execute(
                select(outbox_jobs.c.state, outbox_jobs.c.lease_token, outbox_jobs.c.lease_until)
                .where(
                    outbox_jobs.c.id == work_item.lease.job_id,
                    outbox_jobs.c.kind == "RECOMPUTE",
                    outbox_jobs.c.recompute_request_id == work_item.request_id,
                )
                .with_for_update()
            )
            lease_row = lease.one_or_none()
            if (
                lease_row is None
                or lease_row.state != "LEASED"
                or lease_row.lease_token != work_item.lease.lease_token
                or lease_row.lease_until is None
                or lease_row.lease_until <= now
            ):
                raise PersistenceConflictError(resource="outbox lease")
            request = await session.execute(
                select(recompute_requests.c.user_id, recompute_requests.c.profile_version)
                .select_from(
                    recompute_requests.join(
                        recompute_contexts,
                        recompute_contexts.c.request_id == recompute_requests.c.id,
                    )
                )
                .where(
                    recompute_requests.c.id == work_item.request_id,
                    recompute_requests.c.state == "RUNNING",
                )
                .with_for_update()
            )
            request_row = request.one_or_none()
            if (
                request_row is None
                or request_row.user_id != work_item.user_id
                or request_row.profile_version != work_item.profile_version
            ):
                raise MissingRecordError(resource="recompute request")
            goal_owner = await session.scalar(
                select(goals.c.user_id).where(goals.c.id == analysis.goal_id)
            )
            if goal_owner != work_item.user_id:
                raise MissingRecordError(resource="goal")
            await session.execute(
                insert(analyses).values(
                    id=analysis.analysis_id,
                    user_id=analysis.user_id,
                    goal_id=analysis.goal_id,
                    profile_version=analysis.profile_version,
                    basis_type=analysis.basis_type,
                    basis_version=analysis.basis_version,
                    release_id=analysis.release_id,
                    methodology_version=analysis.methodology_version,
                    status=analysis.status,
                    reference_at=analysis.reference_at,
                    input_snapshot=dict(analysis.input_snapshot),
                    input_hash=analysis.input_hash,
                    results=None if analysis.results is None else dict(analysis.results),
                    is_fixture=analysis.is_fixture,
                    generated_at=now,
                )
            )
            proposal_id = None
            if proposal is not None:
                proposal_id = proposal.proposal_id
                await session.execute(
                    insert(calculation_traces).values(
                        id=proposal.trace_id,
                        user_id=proposal.user_id,
                        kind="ROUTE",
                        input_hash=proposal.proposal_hash,
                        versions=dict(proposal.trace_versions),
                        release_id=proposal.release_id,
                        outputs=dict(proposal.trace_outputs),
                    )
                )
                await session.execute(
                    insert(route_proposals).values(
                        id=proposal.proposal_id,
                        analysis_id=proposal.analysis_id,
                        user_id=proposal.user_id,
                        proposal_hash=proposal.proposal_hash,
                        profile_version=proposal.profile_version,
                        constraints_snapshot=dict(proposal.constraints_snapshot),
                        feasibility=proposal.feasibility,
                        optimization_status=proposal.optimization_status,
                        steps=[dict(step) for step in proposal.steps],
                        decision_trace_id=proposal.trace_id,
                    )
                )
            completed_request = await session.execute(
                update(recompute_requests)
                .where(
                    recompute_requests.c.id == work_item.request_id,
                    recompute_requests.c.state == "RUNNING",
                )
                .values(
                    state="READY",
                    resulting_analysis_id=analysis.analysis_id,
                    proposal_id=proposal_id,
                    updated_at=now,
                )
            )
            if completed_request.rowcount != 1:
                raise PersistenceConflictError(resource="recompute request")
            pointer = await session.execute(
                update(profiles)
                .where(
                    profiles.c.user_id == work_item.user_id,
                    profiles.c.version == work_item.profile_version,
                )
                .values(latest_analysis_id=analysis.analysis_id)
            )
            completed_job = await session.execute(
                update(outbox_jobs)
                .where(
                    outbox_jobs.c.id == work_item.lease.job_id,
                    outbox_jobs.c.kind == "RECOMPUTE",
                    outbox_jobs.c.recompute_request_id == work_item.request_id,
                    outbox_jobs.c.lease_token == work_item.lease.lease_token,
                    outbox_jobs.c.state == "LEASED",
                    outbox_jobs.c.lease_until > func.clock_timestamp(),
                )
                .values(state="COMPLETED", lease_token=None, lease_until=None)
            )
            if completed_job.rowcount != 1:
                raise PersistenceConflictError(resource="outbox lease")
        return RecomputeFinalization(
            request_id=work_item.request_id,
            analysis_id=analysis.analysis_id,
            proposal_id=proposal_id,
            updated_latest_analysis=pointer.rowcount == 1,
        )

    async def fail_recompute(self, work_item: RecomputeWorkItem, error_code: str) -> None:
        now = datetime.now(UTC)
        async with self._database.sessions.begin() as session:
            lease = await session.execute(
                select(outbox_jobs.c.state, outbox_jobs.c.lease_token, outbox_jobs.c.lease_until)
                .where(
                    outbox_jobs.c.id == work_item.lease.job_id,
                    outbox_jobs.c.kind == "RECOMPUTE",
                    outbox_jobs.c.recompute_request_id == work_item.request_id,
                )
                .with_for_update()
            )
            lease_row = lease.one_or_none()
            if (
                lease_row is None
                or lease_row.state != "LEASED"
                or lease_row.lease_token != work_item.lease.lease_token
                or lease_row.lease_until is None
                or lease_row.lease_until <= now
            ):
                raise PersistenceConflictError(resource="outbox lease")
            request = await session.execute(
                select(recompute_requests.c.user_id, recompute_requests.c.profile_version)
                .select_from(
                    recompute_requests.join(
                        recompute_contexts,
                        recompute_contexts.c.request_id == recompute_requests.c.id,
                    )
                )
                .where(
                    recompute_requests.c.id == work_item.request_id,
                    recompute_requests.c.state == "RUNNING",
                )
                .with_for_update()
            )
            request_row = request.one_or_none()
            if (
                request_row is None
                or request_row.user_id != work_item.user_id
                or request_row.profile_version != work_item.profile_version
            ):
                raise MissingRecordError(resource="recompute request")
            failed_request = await session.execute(
                update(recompute_requests)
                .where(
                    recompute_requests.c.id == work_item.request_id,
                    recompute_requests.c.state == "RUNNING",
                )
                .values(state="FAILED", error_code=error_code, updated_at=now)
            )
            if failed_request.rowcount != 1:
                raise PersistenceConflictError(resource="recompute request")
            completed_job = await session.execute(
                update(outbox_jobs)
                .where(
                    outbox_jobs.c.id == work_item.lease.job_id,
                    outbox_jobs.c.kind == "RECOMPUTE",
                    outbox_jobs.c.recompute_request_id == work_item.request_id,
                    outbox_jobs.c.lease_token == work_item.lease.lease_token,
                    outbox_jobs.c.state == "LEASED",
                    outbox_jobs.c.lease_until > func.clock_timestamp(),
                )
                .values(state="COMPLETED", lease_token=None, lease_until=None)
            )
            if completed_job.rowcount != 1:
                raise PersistenceConflictError(resource="outbox lease")
