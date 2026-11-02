from datetime import timedelta
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import Interval, bindparam, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.admissions import (
    AdmissionDenied,
    access_allowed,
    admission_for_login,
    attach_admission,
    lock_identity,
    subject_digest,
)
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
    account_access,
    auth_sessions,
    google_identities,
    oauth_login_attempts,
    profiles,
    users,
)

OAUTH_LOGIN_ATTEMPT_LIFETIME = timedelta(minutes=10)


async def prune_auth_records(session: AsyncSession, *, limit: int = 1000) -> None:
    """Delete unusable credentials in bounded batches without locking active sessions."""
    await session.execute(delete(auth_sessions).where(auth_sessions.c.id.in_(
        select(auth_sessions.c.id)
        .where(or_(auth_sessions.c.expires_at <= func.current_timestamp(),
                   auth_sessions.c.revoked_at.is_not(None)))
        .order_by(auth_sessions.c.expires_at, auth_sessions.c.id)
        .limit(limit).with_for_update(skip_locked=True)
    )))
    await session.execute(delete(oauth_login_attempts).where(oauth_login_attempts.c.state_hash.in_(
        select(oauth_login_attempts.c.state_hash)
        .where(oauth_login_attempts.c.expires_at <= func.current_timestamp())
        .order_by(oauth_login_attempts.c.expires_at, oauth_login_attempts.c.state_hash)
        .limit(limit).with_for_update(skip_locked=True)
    )))


class PostgresAuthStore:
    def __init__(self, database: Database, *, invitation_required: bool = True) -> None:
        self._database = database
        self._invitation_required = invitation_required

    async def create_oauth_login_attempt(self, attempt: OAuthLoginAttempt) -> None:
        async with self._database.sessions.begin() as session:
            await prune_auth_records(session)
            await session.execute(
                insert(oauth_login_attempts).values(
                    state_hash=attempt.state_hash,
                    browser_binding_hash=attempt.browser_binding_hash,
                    nonce=attempt.nonce.get_secret_value(),
                    pkce_verifier=attempt.pkce_verifier.get_secret_value(),
                    invitation_hash=attempt.invitation_hash,
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
                    oauth_login_attempts.c.invitation_hash,
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        return ConsumedOAuthLoginAttempt(
            nonce=SecretStr(row.nonce),
            pkce_verifier=SecretStr(row.pkce_verifier),
            invitation_hash=row.invitation_hash,
        )

    async def create_google_session(self, login: GoogleLogin) -> IssuedSession | None:
        async with self._database.sessions.begin() as session:
            await prune_auth_records(session)
            digest = subject_digest(login.google_subject)
            await lock_identity(session, digest)
            identity = (await session.execute(
                select(google_identities.c.user_id, users.c.status).join(
                    users, google_identities.c.user_id == users.c.id,
                ).where(google_identities.c.subject == login.google_subject,
                        google_identities.c.issuer == GOOGLE_ISSUER).with_for_update(of=users)
            )).one_or_none()
            if identity is not None and identity.status != "ACTIVE":
                if not self._invitation_required:
                    return None
                raise AdmissionDenied("ACCESS_REVOKED")
            admission = await admission_for_login(
                session, digest, login.invitation_hash, required=self._invitation_required,
            )
            user_id = identity.user_id if identity is not None else uuid4()
            if identity is None:
                await session.execute(insert(users).values(id=user_id))
                await session.execute(insert(profiles).values(user_id=user_id))
                await session.execute(insert(google_identities).values(
                    issuer=GOOGLE_ISSUER, subject=login.google_subject, user_id=user_id,
                ))
            await attach_admission(session, admission, user_id)
            if login.display_name is not None:
                await session.execute(
                    update(users).where(users.c.id == user_id).values(display_name=login.display_name)
                )
            session_expiry = func.clock_timestamp() + bindparam(
                "session_lifetime", login.session.lifetime, type_=Interval(),
            )
            if admission["expires_at"] is not None:
                session_expiry = func.least(session_expiry, admission["expires_at"])
            expires_at = await session.scalar(
                insert(auth_sessions)
                .values(
                    id=uuid4(),
                    user_id=user_id,
                    token_hash=login.session.token_hash,
                    csrf_hash=login.session.csrf_hash,
                    expires_at=session_expiry,
                )
                .returning(auth_sessions.c.expires_at)
            )
        if expires_at is None:
            return None
        return IssuedSession(user_id=user_id, expires_at=expires_at)

    async def resolve_session(self, session_token_hash: bytes) -> SessionPrincipal | None:
        async with self._database.sessions() as session:
            result = await session.execute(
                select(auth_sessions.c.user_id, auth_sessions.c.csrf_hash, users.c.display_name)
                .join(users, users.c.id == auth_sessions.c.user_id)
                .outerjoin(account_access, account_access.c.user_id == users.c.id)
                .where(
                    *(access_allowed() if self._invitation_required else ()),
                    auth_sessions.c.token_hash == session_token_hash,
                    auth_sessions.c.expires_at > func.current_timestamp(),
                    auth_sessions.c.revoked_at.is_(None),
                    users.c.status == "ACTIVE",
                    select(google_identities.c.user_id).where(
                        google_identities.c.user_id == users.c.id,
                        google_identities.c.issuer == GOOGLE_ISSUER,
                    ).exists(),
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        return SessionPrincipal(user_id=row.user_id, csrf_hash=row.csrf_hash, display_name=row.display_name)

    async def matches_session_csrf(self, session_token_hash: bytes, csrf_hash: bytes) -> bool:
        async with self._database.sessions() as session:
            session_id = await session.scalar(
                select(auth_sessions.c.id)
                .join(users, users.c.id == auth_sessions.c.user_id)
                .outerjoin(account_access, account_access.c.user_id == users.c.id)
                .where(
                    *(access_allowed() if self._invitation_required else ()),
                    auth_sessions.c.token_hash == session_token_hash,
                    auth_sessions.c.csrf_hash == csrf_hash,
                    auth_sessions.c.expires_at > func.current_timestamp(),
                    auth_sessions.c.revoked_at.is_(None),
                    users.c.status == "ACTIVE",
                    select(google_identities.c.user_id).where(
                        google_identities.c.user_id == users.c.id,
                        google_identities.c.issuer == GOOGLE_ISSUER,
                    ).exists(),
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
