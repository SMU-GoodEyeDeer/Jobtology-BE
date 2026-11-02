from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import anyio
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, update

from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.accounts import PostgresAccountService
from jobtology_be.infrastructure.persistence.admissions import AdmissionDenied, subject_digest
from jobtology_be.infrastructure.persistence.auth_contracts import GoogleLogin, SessionIssue
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.management import PostgresManagementStore
from jobtology_be.modules.auth.invitations import invitation_digest

pytest_plugins = ("test_acceptance_persistence",)


def login(subject, code=None):
    return GoogleLogin(
        subject,
        SessionIssue(
            sha256(uuid4().bytes).digest(), sha256(uuid4().bytes).digest(), timedelta(days=7)
        ),
        display_name="테스트 사용자",
        invitation_hash=invitation_digest(code) if code else None,
    )


async def create_code(management, **values):
    return await management.create_invitation(
        {"label": "테스트 초대", "expires_at": None, "access_hours": 2, "max_uses": 1, **values}
    )


def test_relogin_does_not_redeem_again_or_extend_window_and_expired_sessions_fail(
    acceptance_database_url,
):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            auth = PostgresAuthStore(database, invitation_required=True)
            management = PostgresManagementStore(database)
            created = await create_code(management)
            first = login("fixed-window", created["code"])
            issued = await auth.create_google_session(first)
            assert issued is not None
            again = await auth.create_google_session(login("fixed-window"))
            assert again.user_id == issued.user_id
            async with database.sessions() as session:
                access = (await session.execute(select(s.account_access))).mappings().one()
                assert access["expires_at"] - access["granted_at"] == timedelta(hours=2)
                assert await session.scalar(select(s.invitation_codes.c.use_count)) == 1
            assert await auth.resolve_session(first.session.token_hash) is not None
            await management.update_user(
                issued.user_id, {"access_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
            assert await auth.resolve_session(first.session.token_hash) is None
            async with database.sessions() as session:
                expires = await session.scalar(
                    select(s.auth_sessions.c.expires_at).where(
                        s.auth_sessions.c.token_hash == first.session.token_hash,
                    )
                )
                assert expires < datetime.now(UTC)
            assert not await auth.matches_session_csrf(
                first.session.token_hash, first.session.csrf_hash
            )
            with pytest.raises(AdmissionDenied, match="ACCESS_EXPIRED"):
                await auth.create_google_session(login("fixed-window", created["code"]))
        finally:
            await database.dispose()

    anyio.run(exercise)


@pytest.mark.parametrize("reason", ["missing", "expired", "revoked", "full"])
def test_denied_admission_never_creates_an_account_or_session(acceptance_database_url, reason):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            management = PostgresManagementStore(database)
            code = await create_code(
                management,
                expires_at=(datetime.now(UTC) - timedelta(days=1)) if reason == "expired" else None,
            )
            if reason == "revoked":
                await management.revoke_invitation(code["invitation"]["id"])
            if reason == "full":
                async with database.sessions.begin() as session:
                    await session.execute(update(s.invitation_codes).values(use_count=1))
            auth = PostgresAuthStore(database, invitation_required=True)
            with pytest.raises(AdmissionDenied):
                await auth.create_google_session(
                    login("denied", None if reason == "missing" else code["code"])
                )
            async with database.sessions() as session:
                for table in (s.users, s.profiles, s.auth_sessions, s.account_access):
                    assert await session.scalar(select(func.count()).select_from(table)) == 0
        finally:
            await database.dispose()

    anyio.run(exercise)


def test_last_code_slots_are_atomic_across_distinct_accounts(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            management = PostgresManagementStore(database)
            code = await create_code(management, max_uses=3, access_hours=None)
            auth = PostgresAuthStore(database, invitation_required=True)
            results = []

            async def sign_in(index):
                try:
                    results.append(
                        await auth.create_google_session(login(str(index), code["code"]))
                    )
                except AdmissionDenied:
                    results.append(None)

            async with anyio.create_task_group() as group:
                for index in range(8):
                    group.start_soon(sign_in, index)
            assert sum(result is not None for result in results) == 3
            async with database.sessions() as session:
                assert await session.scalar(select(s.invitation_codes.c.use_count)) == 3
                assert await session.scalar(select(func.count()).select_from(s.users)) == 3
                assert await session.scalar(select(func.count()).select_from(s.auth_sessions)) == 3
        finally:
            await database.dispose()

    anyio.run(exercise)


def test_deleted_account_cannot_restart_trial_or_redeem_a_second_code(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            auth = PostgresAuthStore(database, invitation_required=True)
            management = PostgresManagementStore(database)
            code = await create_code(management)
            issued = await auth.create_google_session(login("deleted-subject", code["code"]))
            async with database.sessions() as session:
                expiry = await session.scalar(select(s.account_access.c.expires_at))
            await PostgresAccountService(database).delete_account(issued.user_id)
            async with database.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(s.users)) == 0
                assert await session.scalar(select(s.account_access.c.user_id)) is None
            recreated = await auth.create_google_session(login("deleted-subject", code["code"]))
            assert recreated.user_id != issued.user_id
            async with database.sessions() as session:
                assert await session.scalar(select(s.account_access.c.expires_at)) == expiry
                assert await session.scalar(select(s.invitation_codes.c.use_count)) == 1
            await management.update_user(recreated.user_id, {"status": "DEACTIVATED"})
            assert await auth.resolve_session(sha256(b"unknown").digest()) is None
            with pytest.raises(AdmissionDenied, match="ACCESS_REVOKED"):
                await auth.create_google_session(login("deleted-subject"))
            await management.update_user(recreated.user_id, {"status": "ACTIVE"})
            assert await auth.create_google_session(login("deleted-subject")) is not None
        finally:
            await database.dispose()

    anyio.run(exercise)


def test_management_views_never_return_credentials_and_can_revoke_sessions(acceptance_database_url):
    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            management = PostgresManagementStore(database)
            code = await create_code(management)
            auth = PostgresAuthStore(database, invitation_required=True)
            first = login("managed", code["code"])
            issued = await auth.create_google_session(first)
            assert code["code"] not in str(await management.invitations(50, 0))
            assert "code_hash" not in str(await management.invitations(50, 0))
            sessions = await management.sessions(issued.user_id, 50, 0)
            assert "token_hash" not in str(sessions) and "csrf_hash" not in str(sessions)
            assert (await management.users(50, 0))["items"][0]["active_sessions"] == 1
            await management.revoke_sessions(session_id=sessions["items"][0]["id"])
            assert await auth.resolve_session(first.session.token_hash) is None
            again = login("managed")
            await auth.create_google_session(again)
            await management.revoke_sessions(user_id=issued.user_id)
            assert await auth.resolve_session(again.session.token_hash) is None
            await management.revoke_invitation(code["invitation"]["id"])
            # Revoking a code stops enrollment, not an already admitted user.
            assert await auth.create_google_session(login("managed")) is not None
        finally:
            await database.dispose()

    anyio.run(exercise)


def test_migration_grandfathers_existing_google_accounts_without_resetting_on_replay(
    acceptance_database_url,
):
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", acceptance_database_url)
    command.downgrade(config, "20261020_02")
    user_id = uuid4()

    async def seed():
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions.begin() as session:
                await session.execute(insert(s.users).values(id=user_id))
                await session.execute(insert(s.profiles).values(user_id=user_id))
                await session.execute(
                    insert(s.google_identities).values(
                        user_id=user_id, issuer="https://accounts.google.com", subject="legacy"
                    )
                )
        finally:
            await database.dispose()

    anyio.run(seed)
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    async def verify():
        database = Database.create(acceptance_database_url)
        try:
            issued = await PostgresAuthStore(
                database, invitation_required=True
            ).create_google_session(login("legacy"))
            assert issued.user_id == user_id
            async with database.sessions() as session:
                access = (await session.execute(select(s.account_access))).mappings().one()
                assert access["source"] == "LEGACY" and access["expires_at"] is None
                assert access["subject_hash"] == subject_digest("legacy")
        finally:
            await database.dispose()

    anyio.run(verify)


def test_concurrent_same_identity_consumes_one_use_and_sql_failure_rolls_back_redemption(
    acceptance_database_url,
):
    from sqlalchemy.exc import IntegrityError

    async def exercise():
        database = Database.create(acceptance_database_url)
        try:
            management = PostgresManagementStore(database)
            created = await create_code(management, max_uses=3)
            auth = PostgresAuthStore(database)
            logins = [login("same-google-subject", created["code"]) for _ in range(2)]
            results = []

            async def sign_in(command):
                results.append(await auth.create_google_session(command))

            async with anyio.create_task_group() as group:
                for command in logins:
                    group.start_soon(sign_in, command)
            assert results[0].user_id == results[1].user_id
            collision = GoogleLogin(
                "another-google-subject",
                logins[0].session,
                invitation_hash=invitation_digest(created["code"]),
            )
            with pytest.raises(IntegrityError):
                await auth.create_google_session(collision)
            async with database.sessions() as session:
                assert await session.scalar(select(s.invitation_codes.c.use_count)) == 1
                assert await session.scalar(select(func.count()).select_from(s.users)) == 1
                assert await session.scalar(select(func.count()).select_from(s.account_access)) == 1
        finally:
            await database.dispose()

    anyio.run(exercise)
