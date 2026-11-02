"""Admission checks use database time and serialize distinct Google identities."""

from datetime import timedelta
from hashlib import sha256

from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from jobtology_be.infrastructure.persistence.schema import account_access, invitation_codes


class AdmissionDenied(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def subject_digest(subject: str) -> bytes:
    return sha256(("https://accounts.google.com\0" + subject).encode()).digest()


def access_allowed():
    return (
        account_access.c.user_id.is_not(None),
        account_access.c.revoked_at.is_(None),
        or_(
            account_access.c.expires_at.is_(None),
            account_access.c.expires_at > func.current_timestamp(),
        ),
    )


async def lock_identity(session: AsyncSession, digest: bytes) -> None:
    # Serializes first-time admission even before a user/admission row exists.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(digest[:8], "big", signed=True)},
    )


async def admission_for_login(
    session: AsyncSession, digest: bytes, invitation_hash: bytes | None, *, required: bool
) -> dict:
    admission = (
        (
            await session.execute(
                select(account_access)
                .where(
                    account_access.c.subject_hash == digest,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if admission is not None:
        if required and admission["revoked_at"] is not None:
            raise AdmissionDenied("ACCESS_REVOKED")
        if required and admission["expires_at"] is not None and admission["expires_at"] <= now:
            raise AdmissionDenied("ACCESS_EXPIRED")
        return dict(admission)
    if not required:
        return {"subject_hash": digest, "source": "LEGACY", "expires_at": None}
    if invitation_hash is None:
        raise AdmissionDenied("INVITATION_REQUIRED")
    code = (
        (
            await session.execute(
                select(invitation_codes)
                .where(
                    invitation_codes.c.code_hash == invitation_hash,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if (
        code is None
        or code["revoked_at"] is not None
        or (code["expires_at"] is not None and code["expires_at"] <= now)
        or (code["max_uses"] is not None and code["use_count"] >= code["max_uses"])
    ):
        raise AdmissionDenied("INVITATION_INVALID")
    await session.execute(
        update(invitation_codes)
        .where(invitation_codes.c.id == code["id"])
        .values(use_count=invitation_codes.c.use_count + 1)
    )
    return {
        "subject_hash": digest,
        "source": "INVITATION",
        "invitation_id": code["id"],
        "granted_at": now,
        "expires_at": now + timedelta(hours=code["access_hours"])
        if code["access_hours"] is not None
        else None,
    }


async def attach_admission(session: AsyncSession, admission: dict, user_id) -> None:
    if "user_id" in admission:
        await session.execute(
            update(account_access)
            .where(
                account_access.c.subject_hash == admission["subject_hash"],
            )
            .values(user_id=user_id)
        )
    else:
        await session.execute(insert(account_access).values(**admission, user_id=user_id))
