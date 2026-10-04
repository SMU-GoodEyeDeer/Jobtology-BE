from datetime import timedelta

import anyio
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from jobtology_be.infrastructure.persistence.auth_contracts import GuestSessionUnavailableError
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import auth_sessions, profiles, users
from jobtology_be.modules.auth.session import issue_session_credentials

pytest_plugins = ("test_acceptance_persistence",)


async def _counts(database: Database) -> tuple[int, int, int]:
    async with database.sessions() as session:
        counts = [
            (await session.execute(select(func.count()).select_from(table))).scalar_one()
            for table in (users, profiles, auth_sessions)
        ]
        return counts[0], counts[1], counts[2]


def test_guest_creation_rolls_back_when_profile_insert_returns_no_version(
    acceptance_database_url: str,
) -> None:
    async def scenario() -> None:
        # Given
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                await session.execute(text(
                    "CREATE FUNCTION skip_profile() RETURNS trigger LANGUAGE plpgsql "
                    "AS $$ BEGIN RETURN NULL; END $$"
                ))
                await session.execute(text(
                    "CREATE TRIGGER skip_profile BEFORE INSERT ON profiles "
                    "FOR EACH ROW EXECUTE FUNCTION skip_profile()"
                ))
            store = PostgresAuthStore(database)
            # When
            with pytest.raises(GuestSessionUnavailableError):
                await store.create_guest_session(
                    issue_session_credentials().session_issue(timedelta(days=7)), 30
                )
            # Then
            assert await _counts(database) == (0, 0, 0)
        finally:
            await database.dispose()

    anyio.run(scenario)


def test_guest_creation_rolls_back_when_session_insert_fails(
    acceptance_database_url: str,
) -> None:
    async def scenario() -> None:
        # Given
        database = Database.create(acceptance_database_url)
        try:
            store = PostgresAuthStore(database)
            issue = issue_session_credentials().session_issue(timedelta(days=7))
            await store.create_guest_session(issue, 30)
            # When
            with pytest.raises(IntegrityError):
                await store.create_guest_session(issue, 30)
            # Then
            assert await _counts(database) == (1, 1, 1)
        finally:
            await database.dispose()

    anyio.run(scenario)


def test_guest_issuance_limit_serializes_concurrent_creations(
    acceptance_database_url: str,
) -> None:
    async def scenario() -> None:
        # Given
        database = Database.create(acceptance_database_url)
        successes: list[bool] = []
        try:
            async def issue() -> None:
                issued = await PostgresAuthStore(database).create_guest_session(
                    issue_session_credentials().session_issue(timedelta(days=7)), 2
                )
                successes.append(issued is not None)
            # When
            async with anyio.create_task_group() as tasks:
                for _ in range(8):
                    tasks.start_soon(issue)
            # Then
            assert sum(successes) == 2
            assert await _counts(database) == (2, 2, 2)
        finally:
            await database.dispose()

    anyio.run(scenario)


def test_revoked_sessions_count_until_issuance_window_expires(
    acceptance_database_url: str,
) -> None:
    async def scenario() -> None:
        # Given
        database = Database.create(acceptance_database_url)
        try:
            store = PostgresAuthStore(database)
            credentials = issue_session_credentials()
            await store.create_guest_session(credentials.session_issue(timedelta(days=7)), 1)
            await store.revoke_session(credentials.hashes.token_hash)
            # When
            limited = await store.create_guest_session(
                issue_session_credentials().session_issue(timedelta(days=7)), 1
            )
            # Then
            assert limited is None
            assert await _counts(database) == (1, 1, 1)
            async with database.sessions.begin() as session:
                await session.execute(update(auth_sessions).values(
                    created_at=func.clock_timestamp() - text("INTERVAL '61 seconds'")
                ))
            assert await store.create_guest_session(
                issue_session_credentials().session_issue(timedelta(days=7)), 1
            ) is not None
        finally:
            await database.dispose()

    anyio.run(scenario)
