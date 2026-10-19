from datetime import UTC, datetime, timedelta
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.exc import OperationalError

from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.accounts import PostgresAccountService
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database

pytest_plugins = ("test_acceptance_persistence",)

USER_TABLES = (
    "users",
    "profiles",
    "google_identities",
    "auth_sessions",
    "user_state_events",
    "user_capabilities",
    "goals",
    "route_preferences",
    "analyses",
    "calculation_traces",
    "route_proposals",
    "roadmaps",
    "roadmap_steps",
    "step_dependencies",
    "step_completion_inheritances",
    "recompute_requests",
    "recompute_contexts",
    "outbox_jobs",
    "idempotency_records",
    "roadmap_plan_overrides",
    "account_rate_limits",
)


async def seed_account(session, *, google=False, inactive=False):
    ids = {
        name: uuid4()
        for name in (
            "user",
            "goal",
            "event",
            "analysis",
            "trace",
            "proposal",
            "roadmap",
            "step1",
            "step2",
            "request",
            "job",
            "session",
            "capability",
        )
    }
    u = ids["user"]
    now = datetime.now(UTC)
    await session.execute(
        insert(s.users).values(id=u, status="DEACTIVATED" if inactive else "ACTIVE")
    )
    await session.execute(insert(s.profiles).values(user_id=u))
    if google:
        await session.execute(
            insert(s.google_identities).values(
                user_id=u, issuer="https://accounts.google.com", subject=str(u)
            )
        )
    await session.execute(
        insert(s.auth_sessions).values(
            id=ids["session"],
            user_id=u,
            token_hash=u.bytes * 2,
            csrf_hash=u.bytes * 2,
            expires_at=now + timedelta(days=1),
        )
    )
    await session.execute(
        insert(s.user_state_events).values(
            id=ids["event"],
            user_id=u,
            aggregate_id=u,
            aggregate_type="PROFILE",
            version=1,
            kind="ProfileUpdated",
            payload={},
        )
    )
    await session.execute(
        insert(s.user_capabilities).values(
            id=ids["capability"],
            user_id=u,
            category="chat",
            raw_text="local test",
            source_completion_event_id=ids["event"],
        )
    )
    await session.execute(
        insert(s.goals).values(
            id=ids["goal"],
            user_id=u,
            goal_mode="TARGETED",
            occupation_id="BACKEND_DEVELOPER",
            target_by=now + timedelta(days=90),
            timezone="UTC",
            original_time_phrase="90 days",
            status="ACTIVE",
        )
    )
    await session.execute(
        insert(s.route_preferences).values(
            user_id=u, available_hours_per_week=10, availability_source="USER", budget_mode="SOFT"
        )
    )
    await session.execute(
        insert(s.analyses).values(
            id=ids["analysis"],
            user_id=u,
            goal_id=ids["goal"],
            profile_version=1,
            basis_type="EDITORIAL",
            basis_version="local-test",
            methodology_version="local-test",
            status="SUCCEEDED",
            reference_at=now,
            input_snapshot={},
            input_hash=str(u),
            results={},
        )
    )
    await session.execute(
        insert(s.calculation_traces).values(
            id=ids["trace"], user_id=u, kind="ROUTE", input_hash=str(u), versions={}, outputs={}
        )
    )
    await session.execute(
        insert(s.route_proposals).values(
            id=ids["proposal"],
            analysis_id=ids["analysis"],
            user_id=u,
            proposal_hash=str(u),
            profile_version=1,
            constraints_snapshot={},
            feasibility="FEASIBLE",
            optimization_status="OPTIMAL",
            steps=[],
            decision_trace_id=ids["trace"],
        )
    )
    await session.execute(
        insert(s.roadmaps).values(
            id=ids["roadmap"],
            user_id=u,
            goal_id=ids["goal"],
            proposal_id=ids["proposal"],
            title="local test",
            state="ACTIVE",
            profile_version=1,
        )
    )
    for index, name in enumerate(("step1", "step2")):
        await session.execute(
            insert(s.roadmap_steps).values(
                id=ids[name],
                roadmap_id=ids["roadmap"],
                step_key=name,
                position=index,
                action_id=name,
                template_revision=1,
            )
        )
    await session.execute(
        insert(s.step_dependencies).values(
            roadmap_id=ids["roadmap"], step_id=ids["step2"], prerequisite_step_id=ids["step1"]
        )
    )
    await session.execute(
        insert(s.step_completion_inheritances).values(
            step_id=ids["step1"], original_completion_event_id=ids["event"]
        )
    )
    await session.execute(
        insert(s.roadmap_plan_overrides).values(
            roadmap_id=ids["roadmap"], weekly_hours=10, rest_weeks=[], step_order=[], step_hours={}
        )
    )
    await session.execute(
        insert(s.recompute_requests).values(
            id=ids["request"],
            user_id=u,
            profile_version=1,
            trigger_event_id=ids["event"],
            state="SUCCEEDED",
            resulting_analysis_id=ids["analysis"],
            proposal_id=ids["proposal"],
        )
    )
    await session.execute(
        insert(s.recompute_contexts).values(request_id=ids["request"], payload={})
    )
    await session.execute(
        insert(s.outbox_jobs).values(
            id=ids["job"],
            kind="RECOMPUTE",
            payload={},
            recompute_request_id=ids["request"],
            dedupe_key=str(u),
        )
    )
    await session.execute(
        insert(s.idempotency_records).values(
            user_id=u,
            method="POST",
            path="/local-test",
            key="local-test",
            request_hash=str(u),
            expires_at=now + timedelta(days=1),
        )
    )
    await session.execute(
        insert(s.account_rate_limits).values(
            user_id=u, scope="chat", window_started_at=now, request_count=1
        )
    )
    return u


async def _counts(session):
    return {
        name: await session.scalar(select(func.count()).select_from(getattr(s, name)))
        for name in USER_TABLES
    }


async def _exercise_deletion(database_url, fail_first):
    database = Database.create(database_url)
    accounts = PostgresAccountService(database)
    auth = PostgresAuthStore(database)
    try:
        async with database.sessions.begin() as session:
            target = await seed_account(session, google=True)
            survivor = await seed_account(session, google=True)
            await session.execute(
                insert(s.llm_credentials).values(provider="shared", ciphertext=b"keep")
            )
            await session.execute(
                insert(s.oauth_login_attempts).values(
                    state_hash=b"a" * 32,
                    browser_binding_hash=b"b" * 32,
                    nonce="keep",
                    pkce_verifier="keep",
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
        assert await auth.resolve_session(target.bytes * 2) is not None
        if fail_first:

            def fail_delete(conn, cursor, statement, parameters, context, executemany):
                if statement.startswith("DELETE FROM users "):
                    raise OperationalError("injected failure", None, RuntimeError("test"))

            event.listen(database.engine.sync_engine, "before_cursor_execute", fail_delete)
            try:
                with pytest.raises(OperationalError):
                    await accounts.delete_account(target)
            finally:
                event.remove(database.engine.sync_engine, "before_cursor_execute", fail_delete)
            async with database.sessions() as session:
                assert all(
                    n == (4 if name == "roadmap_steps" else 2)
                    for name, n in (await _counts(session)).items()
                )
            assert await auth.resolve_session(target.bytes * 2) is not None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(accounts.delete_account, target)
            tasks.start_soon(accounts.delete_account, target)
        assert await auth.resolve_session(target.bytes * 2) is None
        assert not await auth.matches_session_csrf(target.bytes * 2, target.bytes * 2)
        assert await auth.resolve_session(survivor.bytes * 2) is not None
        async with database.sessions() as session:
            counts = await _counts(session)
            assert all(n == (2 if name == "roadmap_steps" else 1) for name, n in counts.items()), (
                counts
            )
            assert await session.scalar(select(s.users.c.id)) == survivor
            assert await session.scalar(select(func.count()).select_from(s.llm_credentials)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(s.oauth_login_attempts)) == 1
            )
    finally:
        await database.dispose()


@pytest.mark.parametrize("fail_first", [False, True])
def test_account_deletion_is_complete_isolated_atomic_and_repeatable(
    acceptance_database_url, fail_first
):
    anyio.run(_exercise_deletion, acceptance_database_url, fail_first)


def test_oauth_login_waiting_for_deletion_does_not_lock_the_identity_first(
    acceptance_database_url,
):
    """A concurrent fresh login may create a new empty account after erasure."""
    from hashlib import sha256

    from sqlalchemy import text

    from jobtology_be.infrastructure.persistence.auth_contracts import GoogleLogin, SessionIssue

    async def exercise():
        database = Database.create(acceptance_database_url)
        accounts = PostgresAccountService(database)
        auth = PostgresAuthStore(database)
        deletion_waiting = anyio.Event()
        login_started = anyio.Event()
        login_pid = None
        issued = []

        def observe_queries(conn, cursor, statement, parameters, context, executemany):
            nonlocal login_pid
            if statement.startswith("SELECT profiles.user_id") and "FOR UPDATE" in statement:
                deletion_waiting.set()
            if statement.startswith("SELECT google_identities.user_id") and "JOIN users" in statement:
                login_pid = conn.connection.driver_connection.get_server_pid()
                login_started.set()

        try:
            async with database.sessions.begin() as session:
                target = await seed_account(session, google=True)

            async def login():
                issued.append(await auth.create_google_session(GoogleLogin(
                    google_subject=str(target), display_name="Fresh Google account",
                    session=SessionIssue(
                        token_hash=sha256(b"new-oauth-session").digest(),
                        csrf_hash=sha256(b"new-oauth-csrf").digest(),
                        lifetime=timedelta(days=7),
                    ),
                )))

            # Pause deletion after its user lock, before it can erase the identity.
            async with database.sessions.begin() as blocker:
                await blocker.execute(select(s.profiles.c.user_id).where(
                    s.profiles.c.user_id == target,
                ).with_for_update())
                event.listen(database.engine.sync_engine, "before_cursor_execute", observe_queries)
                with anyio.fail_after(10):
                    async with anyio.create_task_group() as tasks:
                        tasks.start_soon(accounts.delete_account, target)
                        await deletion_waiting.wait()
                        tasks.start_soon(login)
                        await login_started.wait()
                        async with database.sessions.begin() as observer:
                            # Wait for the login's SELECT to be blocked on the user lock.
                            while not await observer.scalar(text(
                                "SELECT cardinality(pg_blocking_pids(:pid)) > 0"
                            ), {"pid": login_pid}):
                                await anyio.sleep(0.01)
                            # Locking both joined tables would already hold this identity,
                            # then deadlock when erasure reaches it. User-only locking
                            # leaves it available while the OAuth request waits.
                            assert await observer.scalar(select(s.google_identities.c.user_id).where(
                                s.google_identities.c.user_id == target,
                            ).with_for_update(nowait=True)) == target
                        await blocker.commit()

            assert len(issued) == 1 and issued[0] is not None
            assert issued[0].user_id != target
            assert await auth.resolve_session(target.bytes * 2) is None
            async with database.sessions() as session:
                counts = await _counts(session)
                for name, count in counts.items():
                    assert count == (1 if name in {
                        "users", "profiles", "google_identities", "auth_sessions",
                    } else 0), (name, count)
                assert await session.scalar(select(s.google_identities.c.user_id)) == issued[0].user_id
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", observe_queries)
            await database.dispose()

    anyio.run(exercise)
