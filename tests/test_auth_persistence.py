from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

import anyio
from pydantic import SecretStr
from sqlalchemy import func, select, text, update

from jobtology_be.infrastructure.persistence.auth_contracts import (
    GOOGLE_ISSUER,
    ConsumedOAuthLoginAttempt,
    GoogleLogin,
    OAuthLoginAttempt,
    SessionIssue,
)
from jobtology_be.infrastructure.persistence.auth_store import PostgresAuthStore
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    auth_sessions,
    google_identities,
    oauth_login_attempts,
    profiles,
    users,
)

pytest_plugins = ("test_acceptance_persistence",)


@dataclass(frozen=True, slots=True)
class AttemptRaceResult:
    consumed_count: int
    stored_browser_binding_hash: bytes
    stored_state_hash: bytes


@dataclass(frozen=True, slots=True)
class GoogleLoginRaceResult:
    identity_count: int
    profile_count: int
    session_count: int
    user_ids: tuple[UUID, ...]


def _digest(value: str) -> bytes:
    return sha256(value.encode()).digest()


async def _consume_attempt(
    store: PostgresAuthStore,
    state_hash: bytes,
    browser_binding_hash: bytes,
    outcomes: list[ConsumedOAuthLoginAttempt | None],
) -> None:
    attempt = await store.consume_oauth_login_attempt(state_hash, browser_binding_hash)
    outcomes.append(attempt)


async def _exercise_attempt_race(database_url: str) -> AttemptRaceResult:
    database = Database.create(database_url)
    store = PostgresAuthStore(database)
    state_hash = _digest("state")
    browser_binding_hash = _digest("browser")
    try:
        await store.create_oauth_login_attempt(
            OAuthLoginAttempt(
                state_hash=state_hash,
                browser_binding_hash=browser_binding_hash,
                nonce=SecretStr("nonce-value"),
                pkce_verifier=SecretStr("pkce-verifier"),
            )
        )
        async with database.sessions() as session:
            stored_attempt = (
                await session.execute(
                    select(
                        oauth_login_attempts.c.state_hash,
                        oauth_login_attempts.c.browser_binding_hash,
                    )
                )
            ).one()
        assert stored_attempt.state_hash == state_hash
        assert stored_attempt.browser_binding_hash == browser_binding_hash
        assert (
            await store.consume_oauth_login_attempt(state_hash, _digest("other-browser"))
        ) is None
        outcomes: list[ConsumedOAuthLoginAttempt | None] = []
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_consume_attempt, store, state_hash, browser_binding_hash, outcomes)
            task_group.start_soon(_consume_attempt, store, state_hash, browser_binding_hash, outcomes)
        consumed_attempt = next(attempt for attempt in outcomes if attempt is not None)
        assert consumed_attempt.nonce.get_secret_value() == "nonce-value"
        assert consumed_attempt.pkce_verifier.get_secret_value() == "pkce-verifier"
        return AttemptRaceResult(
            consumed_count=sum(attempt is not None for attempt in outcomes),
            stored_state_hash=stored_attempt.state_hash,
            stored_browser_binding_hash=stored_attempt.browser_binding_hash,
        )
    finally:
        await database.dispose()


async def _create_google_session(
    store: PostgresAuthStore, label: str, outcomes: list[UUID]
) -> None:
    result = await store.create_google_session(
        GoogleLogin(
            google_subject="google-subject",
            session=SessionIssue(
                token_hash=_digest(f"session-{label}"),
                csrf_hash=_digest(f"csrf-{label}"),
                lifetime=timedelta(days=30),
            ),
        )
    )
    assert result is not None
    outcomes.append(result.user_id)


async def _exercise_google_login_race(database_url: str) -> GoogleLoginRaceResult:
    database = Database.create(database_url)
    store = PostgresAuthStore(database)
    try:
        user_ids: list[UUID] = []
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(_create_google_session, store, "one", user_ids)
            task_group.start_soon(_create_google_session, store, "two", user_ids)
        async with database.sessions() as session:
            identity_count = await session.scalar(select(func.count()).select_from(google_identities))
            profile_count = await session.scalar(select(func.count()).select_from(profiles))
            session_count = await session.scalar(select(func.count()).select_from(auth_sessions))
        assert identity_count is not None
        assert profile_count is not None
        assert session_count is not None
        return GoogleLoginRaceResult(
            identity_count=identity_count,
            profile_count=profile_count,
            session_count=session_count,
            user_ids=tuple(user_ids),
        )
    finally:
        await database.dispose()


async def _exercise_expiry_and_revocation(database_url: str) -> bool:
    database = Database.create(database_url)
    store = PostgresAuthStore(database)
    token_hash = _digest("session")
    csrf_hash = _digest("csrf")
    try:
        issued = await store.create_google_session(
            GoogleLogin(
                google_subject="another-google-subject",
                session=SessionIssue(
                    token_hash=token_hash,
                    csrf_hash=csrf_hash,
                    lifetime=timedelta(days=30),
                ),
            )
        )
        assert issued is not None
        assert (await store.resolve_session(token_hash)).csrf_hash == csrf_hash
        assert await store.matches_session_csrf(token_hash, csrf_hash)
        await store.revoke_session(token_hash)
        assert await store.resolve_session(token_hash) is None
        expired_attempt_hash = _digest("expired-state")
        await store.create_oauth_login_attempt(
            OAuthLoginAttempt(
                state_hash=expired_attempt_hash,
                browser_binding_hash=_digest("expired-browser"),
                nonce=SecretStr("expired-nonce"),
                pkce_verifier=SecretStr("expired-verifier"),
            )
        )
        async with database.sessions.begin() as session:
            await session.execute(
                update(auth_sessions)
                .where(auth_sessions.c.token_hash == token_hash)
                .values(revoked_at=None, expires_at=func.current_timestamp() - text("INTERVAL '1 second'"))
            )
            await session.execute(
                update(oauth_login_attempts)
                .where(oauth_login_attempts.c.state_hash == expired_attempt_hash)
                .values(expires_at=func.current_timestamp() - text("INTERVAL '1 second'"))
            )
            await session.execute(
                update(users).where(users.c.id == issued.user_id).values(status="DEACTIVATED")
            )
        inactive_user_session = await store.create_google_session(
            GoogleLogin(
                google_subject="another-google-subject",
                session=SessionIssue(
                    token_hash=_digest("inactive-session"),
                    csrf_hash=_digest("inactive-csrf"),
                    lifetime=timedelta(days=30),
                ),
            )
        )
        return (
            await store.resolve_session(token_hash) is None
            and await store.consume_oauth_login_attempt(
                expired_attempt_hash, _digest("expired-browser")
            )
            is None
            and inactive_user_session is None
        )
    finally:
        await database.dispose()


def test_oauth_attempt_is_browser_bound_and_consumed_once_on_postgres(
    acceptance_database_url: str,
) -> None:
    # Given / When
    result = anyio.run(_exercise_attempt_race, acceptance_database_url)

    # Then
    assert result.consumed_count == 1
    assert result.stored_state_hash == _digest("state")
    assert result.stored_browser_binding_hash == _digest("browser")


def test_concurrent_google_first_login_creates_one_active_identity_user_and_profile(
    acceptance_database_url: str,
) -> None:
    # Given / When
    result = anyio.run(_exercise_google_login_race, acceptance_database_url)

    # Then
    assert result.identity_count == 1
    assert result.profile_count == 1
    assert result.session_count == 2
    assert len(set(result.user_ids)) == 1


def test_opaque_session_rejects_revoked_and_database_clock_expired_sessions(
    acceptance_database_url: str,
) -> None:
    # Given / When / Then
    assert anyio.run(_exercise_expiry_and_revocation, acceptance_database_url)


def test_google_identity_schema_is_canonical_and_never_stores_email() -> None:
    # Given / When
    identity_columns: Sequence[str] = tuple(google_identities.c.keys())

    # Then
    assert GOOGLE_ISSUER == "https://accounts.google.com"
    assert identity_columns == ("issuer", "subject", "user_id", "created_at")
    assert "email" not in identity_columns
    assert "state" not in oauth_login_attempts.c
    assert "browser_binding" not in oauth_login_attempts.c
    assert "token" not in auth_sessions.c
