"""Delete complete goal aggregates without removing account-wide or other-goal data."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, insert, select, update
from sqlalchemy.exc import OperationalError
from test_acceptance_m5_worker import _context_payload

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.accounts import PostgresAccountService
from jobtology_be.infrastructure.persistence.contracts import (
    AnalysisPersist,
    GoalDelete,
    MissingRecordError,
    OutboxLease,
    PersistenceConflictError,
    RecomputeArtifacts,
    RecomputeWorkItem,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)


class Identity:
    def __init__(self, user_id):
        self.user_id = user_id

    async def current_principal(self):
        return AuthenticatedPrincipal(user_id=self.user_id)


def app(database_url, user_id):
    return create_app(Settings(_env_file=None, database_url=database_url),
                      dependencies=ApiDependencies(identity_provider=Identity(user_id)))


async def seed_user(session):
    user_id = uuid4()
    await session.execute(insert(s.users).values(id=user_id, display_name="Google Name"))
    await session.execute(insert(s.profiles).values(user_id=user_id, version=10))
    await session.execute(insert(s.google_identities).values(
        issuer="https://accounts.google.com", subject=str(user_id), user_id=user_id,
    ))
    await session.execute(insert(s.auth_sessions).values(
        id=uuid4(), user_id=user_id, token_hash=user_id.bytes * 2, csrf_hash=user_id.bytes * 2,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    ))
    await session.execute(insert(s.route_preferences).values(
        user_id=user_id, available_hours_per_week=10, availability_source="USER", budget_mode="REGULAR",
    ))
    return user_id


async def seed_goal(session, user_id, status, *, shared_trace=None):
    ids = {name: uuid4() for name in ("goal", "event", "analysis", "trace", "proposal", "roadmap", "step", "step2", "capability", "request", "job")}
    if shared_trace is not None:
        ids["trace"] = shared_trace
    now = datetime.now(UTC)
    await session.execute(insert(s.goals).values(
        id=ids["goal"], user_id=user_id, goal_mode="TARGETED", occupation_id="BACKEND_DEVELOPER",
        target_by=now + timedelta(days=30), timezone="UTC", original_time_phrase="one month", status=status,
    ))
    await session.execute(insert(s.user_state_events).values(
        id=ids["event"], user_id=user_id, aggregate_id=ids["goal"], aggregate_type="PROFILE",
        version=10, kind="GOAL_UPDATED", payload={},
    ))
    await session.execute(insert(s.analyses).values(
        id=ids["analysis"], user_id=user_id, goal_id=ids["goal"], profile_version=10,
        basis_type="EDITORIAL", basis_version="reviewed-v1", methodology_version="test",
        status="READY", reference_at=now, input_snapshot={}, input_hash=str(ids["goal"]), results={},
    ))
    if shared_trace is None:
        await session.execute(insert(s.calculation_traces).values(
            id=ids["trace"], user_id=user_id, kind="ROUTE", input_hash=str(ids["goal"]), versions={}, outputs={},
        ))
    await session.execute(insert(s.route_proposals).values(
        id=ids["proposal"], user_id=user_id, analysis_id=ids["analysis"], profile_version=10,
        proposal_hash=str(ids["goal"]), constraints_snapshot={}, feasibility="FEASIBLE",
        optimization_status="OPTIMAL", steps=[], decision_trace_id=ids["trace"],
    ))
    await session.execute(insert(s.roadmaps).values(
        id=ids["roadmap"], user_id=user_id, goal_id=ids["goal"], proposal_id=ids["proposal"],
        profile_version=10, state="ACTIVE" if status == "ACTIVE" else "ARCHIVED", title="Goal route",
    ))
    for position, key in enumerate(("step", "step2")):
        await session.execute(insert(s.roadmap_steps).values(
            id=ids[key], roadmap_id=ids["roadmap"], step_key=key, position=position,
            action_id=key, template_revision=1, state="COMPLETED" if key == "step" else "TODO",
        ))
    await session.execute(insert(s.step_dependencies).values(
        roadmap_id=ids["roadmap"], step_id=ids["step2"], prerequisite_step_id=ids["step"],
    ))
    await session.execute(insert(s.step_completion_inheritances).values(
        step_id=ids["step"], original_completion_event_id=ids["event"],
    ))
    await session.execute(insert(s.user_capabilities).values(
        id=ids["capability"], user_id=user_id, category="ROADMAP_OUTCOME", raw_text="Python API",
        entity_id="python-api", source_completion_event_id=ids["event"], details={"experience_codes": []},
    ))
    await session.execute(insert(s.roadmap_plan_overrides).values(
        roadmap_id=ids["roadmap"], weekly_hours=5, rest_weeks=[], step_order=[], step_hours={},
    ))
    analysis_event = uuid4()
    await session.execute(insert(s.user_state_events).values(
        id=analysis_event, user_id=user_id, aggregate_id=analysis_event, aggregate_type="PROFILE",
        version=10, kind="ANALYSIS_REQUESTED", payload={"goal_id": str(ids["goal"])},
    ))
    await session.execute(insert(s.recompute_requests).values(
        id=ids["request"], user_id=user_id, profile_version=10, trigger_event_id=analysis_event,
        state="READY", resulting_analysis_id=ids["analysis"], proposal_id=ids["proposal"],
    ))
    await session.execute(insert(s.recompute_contexts).values(
        request_id=ids["request"], payload=_context_payload(user_id, 10, ids["goal"], target_by=now + timedelta(days=30)),
    ))
    await session.execute(insert(s.outbox_jobs).values(
        id=ids["job"], kind="RECOMPUTE", payload={}, recompute_request_id=ids["request"],
        dedupe_key=str(ids["job"]), state="COMPLETED",
    ))
    return ids


async def seed_work(session, user_id, goal, state, *, trigger_event=None):
    request_id, job_id, lease_token = uuid4(), uuid4(), uuid4()
    if trigger_event is None:
        trigger_event = uuid4()
        await session.execute(insert(s.user_state_events).values(
            id=trigger_event, user_id=user_id, aggregate_id=trigger_event, aggregate_type="PROFILE",
            version=10, kind="ANALYSIS_REQUESTED", payload={"goal_id": str(goal["goal"])},
        ))
    await session.execute(insert(s.recompute_requests).values(
        id=request_id, user_id=user_id, profile_version=10, trigger_event_id=trigger_event, state=state,
    ))
    await session.execute(insert(s.recompute_contexts).values(
        request_id=request_id, payload=_context_payload(
            user_id, 10, goal["goal"], target_by=datetime.now(UTC) + timedelta(days=30),
        ),
    ))
    await session.execute(insert(s.outbox_jobs).values(
        id=job_id, kind="RECOMPUTE", recompute_request_id=request_id, payload={}, dedupe_key=str(job_id),
        state="LEASED" if state == "RUNNING" else "READY",
        lease_token=lease_token if state == "RUNNING" else None,
        lease_until=datetime.now(UTC) + timedelta(minutes=5) if state == "RUNNING" else None,
    ))
    return RecomputeWorkItem(request_id, user_id, 10, OutboxLease(job_id, lease_token, "RECOMPUTE", {}))


async def seed_idempotency(session, user_id, key, response):
    await session.execute(insert(s.idempotency_records).values(
        user_id=user_id, method="POST", path="/api/v1/me/goals", key=key, request_hash=key,
        expires_at=datetime.now(UTC) + timedelta(days=1), response_status=201, response=response,
    ))


@pytest.mark.parametrize("target_status", ["ACTIVE", "DRAFT", "ARCHIVED"])
def test_goal_delete_removes_only_its_full_aggregate_and_preserves_account_and_other_goals(
    acceptance_database_url, target_status,
):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                user = await seed_user(session)
                target = await seed_goal(session, user, target_status)
                other = await seed_goal(session, user, "DRAFT" if target_status == "ACTIVE" else "ACTIVE", shared_trace=target["trace"])
                stranger = await seed_user(session)
                foreign = await seed_goal(session, stranger, "ACTIVE")
                pending = await seed_work(session, user, target, "PENDING")
                running = await seed_work(session, user, target, "RUNNING")
                await session.execute(update(s.outbox_jobs).where(s.outbox_jobs.c.id == foreign["job"]).values(
                    payload={"recompute_request_id": str(pending.request_id)},
                ))
                legacy_job = uuid4()
                await session.execute(insert(s.outbox_jobs).values(
                    id=legacy_job, kind="RECOMPUTE", payload={"recompute_request_id": str(pending.request_id)},
                    dedupe_key=str(legacy_job), state="FAILED",
                ))
                # A draft edit can trigger work for the surviving ACTIVE goal.
                survivor_work = await seed_work(session, user, other, "PENDING", trigger_event=target["event"])
                await session.execute(insert(s.recompute_requests).values(
                    id=uuid4(), user_id=user, profile_version=9, trigger_event_id=target["event"],
                    state="FAILED", error_code="MISSING_RECOMPUTE_CONTEXT",
                ))
                exclusive_trace, exclusive_proposal = uuid4(), uuid4()
                await session.execute(insert(s.calculation_traces).values(
                    id=exclusive_trace, user_id=user, kind="ROUTE", input_hash="exclusive", versions={}, outputs={},
                ))
                await session.execute(insert(s.route_proposals).values(
                    id=exclusive_proposal, user_id=user, analysis_id=target["analysis"], profile_version=10,
                    proposal_hash="exclusive", constraints_snapshot={}, feasibility="FEASIBLE",
                    optimization_status="OPTIMAL", steps=[], decision_trace_id=exclusive_trace,
                ))
                for key, response in {
                    "target_goal": {"goal_id": str(target["goal"])},
                    "target_roadmap": {"roadmap_id": str(target["roadmap"])},
                    "target_job": {"recompute_request_id": str(pending.request_id)},
                    "target_trace": {"decision_trace_id": str(exclusive_trace)},
                    "other_goal": {"goal_id": str(other["goal"])},
                    "shared_trace": {"decision_trace_id": str(target["trace"])},
                }.items():
                    await seed_idempotency(session, user, key, response)
                pointer = target["analysis"] if target_status == "ACTIVE" else other["analysis"]
                await session.execute(update(s.profiles).where(s.profiles.c.user_id == user).values(latest_analysis_id=pointer))
            with TestClient(app(acceptance_database_url, user)) as client:
                response = client.request("DELETE", f"/api/v1/me/goals/{target['goal']}", json={"expected_profile_version": 10})
                assert response.status_code == 200, response.text
                assert response.json() == {"goal_id": str(target["goal"]), "profile_version": 11}
                assert client.get(f"/api/v1/me/goals/{target['goal']}").status_code == 404
                assert client.get(f"/api/v1/roadmaps/{target['roadmap']}").status_code == 404
            async with database.sessions() as session:
                for table, column, ids in (
                    (s.goals, s.goals.c.id, (target["goal"],)),
                    (s.analyses, s.analyses.c.id, (target["analysis"],)),
                    (s.route_proposals, s.route_proposals.c.id, (target["proposal"], exclusive_proposal)),
                    (s.roadmaps, s.roadmaps.c.id, (target["roadmap"],)),
                    (s.roadmap_steps, s.roadmap_steps.c.id, (target["step"], target["step2"])),
                    (s.step_dependencies, s.step_dependencies.c.roadmap_id, (target["roadmap"],)),
                    (s.roadmap_plan_overrides, s.roadmap_plan_overrides.c.roadmap_id, (target["roadmap"],)),
                    (s.step_completion_inheritances, s.step_completion_inheritances.c.step_id, (target["step"],)),
                    (s.recompute_requests, s.recompute_requests.c.id, (target["request"], pending.request_id, running.request_id)),
                    (s.recompute_contexts, s.recompute_contexts.c.request_id, (target["request"], pending.request_id, running.request_id)),
                    (s.outbox_jobs, s.outbox_jobs.c.id, (target["job"], pending.lease.job_id, running.lease.job_id, legacy_job)),
                    (s.calculation_traces, s.calculation_traces.c.id, (exclusive_trace,)),
                ):
                    assert await session.scalar(select(func.count()).select_from(table).where(column.in_(ids))) == 0, table.name
                assert await session.scalar(select(s.calculation_traces.c.id).where(s.calculation_traces.c.id == target["trace"])) is not None
                assert await session.scalar(select(s.outbox_jobs.c.id).where(s.outbox_jobs.c.id == foreign["job"])) is not None
                assert await session.scalar(select(s.recompute_requests.c.id).where(s.recompute_requests.c.id == survivor_work.request_id)) is not None
                assert (await session.scalars(select(s.idempotency_records.c.key).where(s.idempotency_records.c.user_id == user).order_by(s.idempotency_records.c.key))).all() == ["other_goal", "shared_trace"]
                profile = (await session.execute(select(s.profiles.c.version, s.profiles.c.latest_analysis_id).where(s.profiles.c.user_id == user))).one()
                assert profile == (11, None if target_status == "ACTIVE" else other["analysis"])
                assert await session.scalar(select(func.count()).select_from(s.user_capabilities).where(s.user_capabilities.c.user_id == user)) == 2
                assert await session.scalar(select(s.user_state_events.c.id).where(s.user_state_events.c.id == target["event"])) is not None
                assert await session.scalar(select(func.count()).select_from(s.user_state_events).where(s.user_state_events.c.user_id == user, s.user_state_events.c.kind == "GOAL_DELETED")) == 1
                for owner, aggregate in ((user, other), (stranger, foreign)):
                    assert await session.scalar(select(s.goals.c.status).where(s.goals.c.id == aggregate["goal"])) == ("DRAFT" if owner == user and target_status == "ACTIVE" else "ACTIVE")
                    assert await session.scalar(select(s.analyses.c.id).where(s.analyses.c.id == aggregate["analysis"])) is not None
                    assert await session.scalar(select(s.roadmaps.c.id).where(s.roadmaps.c.id == aggregate["roadmap"])) is not None
                    assert await session.scalar(select(s.auth_sessions.c.user_id).where(s.auth_sessions.c.user_id == owner)) == owner
                    assert await session.scalar(select(s.google_identities.c.user_id).where(s.google_identities.c.user_id == owner)) == owner
                new_request = (await session.execute(select(s.recompute_requests.c.state, s.recompute_contexts.c.payload).outerjoin(s.recompute_contexts).where(s.recompute_requests.c.user_id == user, s.recompute_requests.c.profile_version == 11))).one_or_none()
                if target_status == "ACTIVE":
                    assert new_request is None
                else:
                    assert new_request.state == "PENDING"
                    assert new_request.payload["goal_id"] == str(other["goal"])
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_goal_delete_reports_ownership_and_version_errors_without_mutation(acceptance_database_url):
    async def seed():
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                user, stranger = await seed_user(session), await seed_user(session)
                own = await seed_goal(session, user, "ACTIVE")
                foreign = await seed_goal(session, stranger, "ACTIVE")
            return user, own, foreign
        finally:
            await database.dispose()

    user, own, foreign = anyio.run(seed)
    with TestClient(app(acceptance_database_url, user)) as client:
        for goal_id in (foreign["goal"], uuid4()):
            response = client.request("DELETE", f"/api/v1/me/goals/{goal_id}", json={"expected_profile_version": 999})
            assert response.status_code == 404
        stale = client.request("DELETE", f"/api/v1/me/goals/{own['goal']}", json={"expected_profile_version": 9})
        assert stale.status_code == 409
        assert client.get("/api/v1/me/profile").json()["profile_version"] == 10
        assert client.get(f"/api/v1/me/goals/{own['goal']}").status_code == 200
        deleted = client.request("DELETE", f"/api/v1/me/goals/{own['goal']}", json={"expected_profile_version": 10})
        assert deleted.status_code == 200
        missing = client.request("DELETE", f"/api/v1/me/goals/{own['goal']}", json={"expected_profile_version": 11})
        assert missing.status_code == 404


def test_goal_delete_rolls_back_every_descendant_if_any_sql_write_fails(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                user = await seed_user(session)
                target = await seed_goal(session, user, "ACTIVE")
                await seed_idempotency(session, user, "goal", {"goal_id": str(target["goal"])})
                await session.execute(update(s.profiles).where(s.profiles.c.user_id == user).values(latest_analysis_id=target["analysis"]))
            tables = (s.goals, s.analyses, s.route_proposals, s.roadmaps, s.roadmap_steps,
                      s.step_dependencies, s.step_completion_inheritances, s.roadmap_plan_overrides,
                      s.recompute_requests, s.recompute_contexts, s.outbox_jobs,
                      s.calculation_traces, s.idempotency_records, s.user_state_events)
            async with database.sessions() as session:
                counts = [await session.scalar(select(func.count()).select_from(table)) for table in tables]

            def fail_delete(conn, cursor, statement, parameters, context, executemany):
                if statement.startswith("DELETE FROM goals "):
                    raise OperationalError("injected", None, RuntimeError("test"))

            event.listen(database.engine.sync_engine, "before_cursor_execute", fail_delete)
            try:
                with pytest.raises(OperationalError):
                    await PostgresApplicationStore(database).delete_goal(GoalDelete(user, target["goal"], 10))
            finally:
                event.remove(database.engine.sync_engine, "before_cursor_execute", fail_delete)
            async with database.sessions() as session:
                assert [await session.scalar(select(func.count()).select_from(table)) for table in tables] == counts
                assert (await session.execute(select(s.profiles.c.version, s.profiles.c.latest_analysis_id))).one() == (10, target["analysis"])
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_deleted_running_goal_work_cannot_finalize_or_recreate_automatic_roadmap(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        store = PostgresApplicationStore(database)
        try:
            async with database.sessions.begin() as session:
                user = await seed_user(session)
                target = await seed_goal(session, user, "ACTIVE")
                running = await seed_work(session, user, target, "RUNNING")
            assert await store.load_recompute_context(running)
            await store.delete_goal(GoalDelete(user, target["goal"], 10))
            artifacts = RecomputeArtifacts(analysis=AnalysisPersist(
                user_id=user, analysis_id=uuid4(), goal_id=target["goal"], profile_version=10,
                basis_type="EDITORIAL", basis_version="test", release_id=None, methodology_version="test",
                status="READY", reference_at=datetime.now(UTC), input_snapshot={}, input_hash="late", results=None,
                is_fixture=False,
            ), proposal=None)
            with pytest.raises(PersistenceConflictError):
                await store.finalize_recompute(running, artifacts)
            await store.fail_recompute(running, "SOLVER_TIMEOUT")
            await store.reconcile_automatic_roadmap(target["request"], "Late roadmap")
            assert await store.claim_recompute_work(limit=10) == ()
            async with database.sessions() as session:
                for table in (s.goals, s.analyses, s.route_proposals, s.roadmaps, s.recompute_requests, s.outbox_jobs):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await database.dispose()
    anyio.run(exercise)


def test_goal_and_account_deletion_serialize_without_deadlock(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                user = await seed_user(session)
                target = await seed_goal(session, user, "ACTIVE")
            store = PostgresApplicationStore(database)

            async def delete_goal():
                try:
                    await store.delete_goal(GoalDelete(user, target["goal"], 10))
                except MissingRecordError:
                    pass  # Account deletion committed first.

            with anyio.fail_after(5):
                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(delete_goal)
                    tasks.start_soon(PostgresAccountService(database).delete_account, user)
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(s.users)) == 0
                assert await session.scalar(select(func.count()).select_from(s.goals)) == 0
        finally:
            await database.dispose()
    anyio.run(exercise)
