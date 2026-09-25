from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import Interval, bindparam, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.auth_contracts import (
    GOOGLE_ISSUER,
    ConsumedOAuthLoginAttempt,
    GoogleLogin,
    IssuedSession,
    OAuthLoginAttempt,
    SessionPrincipal,
)
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import (
    auth_sessions,
    google_identities,
    oauth_login_attempts,
    profiles,
    users,
)

OAUTH_LOGIN_ATTEMPT_LIFETIME = timedelta(minutes=10)


class PostgresAuthStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_oauth_login_attempt(self, attempt: OAuthLoginAttempt) -> None:
        async with self._database.sessions.begin() as session:
            await session.execute(
                insert(oauth_login_attempts).values(
                    state_hash=attempt.state_hash,
                    browser_binding_hash=attempt.browser_binding_hash,
                    nonce=attempt.nonce.get_secret_value(),
                    pkce_verifier=attempt.pkce_verifier.get_secret_value(),
                    expires_at=func.current_timestamp()
                    + bindparam(
                        "oauth_attempt_lifetime",
                        OAUTH_LOGIN_ATTEMPT_LIFETIME,
                        type_=Interval(),
                    ),
                )
            )

    async def consume_oauth_login_attempt(
        self, state_hash: bytes, browser_binding_hash: bytes
    ) -> ConsumedOAuthLoginAttempt | None:
        async with self._database.sessions.begin() as session:
            result = await session.execute(
                delete(oauth_login_attempts)
                .where(
                    oauth_login_attempts.c.state_hash == state_hash,
                    oauth_login_attempts.c.browser_binding_hash == browser_binding_hash,
                    oauth_login_attempts.c.expires_at > func.current_timestamp(),
                )
                .returning(
                    oauth_login_attempts.c.nonce,
                    oauth_login_attempts.c.pkce_verifier,
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        return ConsumedOAuthLoginAttempt(
            nonce=SecretStr(row.nonce),
            pkce_verifier=SecretStr(row.pkce_verifier),
        )

    async def create_google_session(self, login: GoogleLogin) -> IssuedSession | None:
        async with self._database.sessions.begin() as session:
            user_id = await self._active_user_id(session, login.google_subject)
            if user_id is None:
                candidate_user_id = uuid4()
                await session.execute(insert(users).values(id=candidate_user_id))
                await session.execute(insert(profiles).values(user_id=candidate_user_id))
                user_id = await session.scalar(
                    postgres_insert(google_identities)
                    .values(
                        issuer=GOOGLE_ISSUER,
                        subject=login.google_subject,
                        user_id=candidate_user_id,
                    )
                    .on_conflict_do_nothing()
                    .returning(google_identities.c.user_id)
                )
                if user_id is None:
                    await session.execute(
                        delete(profiles).where(profiles.c.user_id == candidate_user_id)
                    )
                    await session.execute(delete(users).where(users.c.id == candidate_user_id))
                    user_id = await self._active_user_id(session, login.google_subject)
            if user_id is None:
                return None
            expires_at = await session.scalar(
                insert(auth_sessions)
                .values(
                    id=uuid4(),
                    user_id=user_id,
                    token_hash=login.session.token_hash,
                    csrf_hash=login.session.csrf_hash,
                    expires_at=func.current_timestamp()
                    + bindparam(
                        "session_lifetime",
                        login.session.lifetime,
                        type_=Interval(),
                    ),
                )
                .returning(auth_sessions.c.expires_at)
            )
        if expires_at is None:
            return None
        return IssuedSession(user_id=user_id, expires_at=expires_at)

    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None:
        async with self._database.sessions() as session:
            result = await session.execute(
                select(auth_sessions.c.user_id, auth_sessions.c.csrf_hash)
                .join(users, users.c.id == auth_sessions.c.user_id)
                .where(
                    auth_sessions.c.token_hash == session_token_hash,
                    auth_sessions.c.expires_at > func.current_timestamp(),
                    auth_sessions.c.revoked_at.is_(None),
                    users.c.status == "ACTIVE",
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        return SessionPrincipal(user_id=row.user_id, csrf_hash=row.csrf_hash)

    async def matches_session_csrf(self, session_token_hash: bytes, csrf_hash: bytes) -> bool:
        async with self._database.sessions() as session:
            session_id = await session.scalar(
                select(auth_sessions.c.id)
                .join(users, users.c.id == auth_sessions.c.user_id)
                .where(
                    auth_sessions.c.token_hash == session_token_hash,
                    auth_sessions.c.csrf_hash == csrf_hash,
                    auth_sessions.c.expires_at > func.current_timestamp(),
                    auth_sessions.c.revoked_at.is_(None),
                    users.c.status == "ACTIVE",
                )
            )
        return session_id is not None

    async def revoke_session(self, session_token_hash: bytes) -> None:
        async with self._database.sessions.begin() as session:
            await session.execute(
                update(auth_sessions)
                .where(
                    auth_sessions.c.token_hash == session_token_hash,
                    auth_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=func.current_timestamp())
            )

    async def _active_user_id(self, session: AsyncSession, google_subject: str) -> UUID | None:
        return await session.scalar(
            select(google_identities.c.user_id)
            .join(users, users.c.id == google_identities.c.user_id)
            .where(
                google_identities.c.issuer == GOOGLE_ISSUER,
                google_identities.c.subject == google_subject,
                users.c.status == "ACTIVE",
            )
            .with_for_update()
        )
