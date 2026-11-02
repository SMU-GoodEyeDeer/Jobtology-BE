"""Narrow application management operations; no SQL or secrets accepted from callers."""

from uuid import UUID, uuid4

from sqlalchemy import func, insert, select, update

from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.modules.auth.invitations import invitation_digest, new_invitation_code


class ManagementNotFound(Exception):
    pass


class ManagementConflict(Exception):
    pass


class PostgresManagementStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    async def _audit(session, action: str, target: UUID) -> None:
        await session.execute(
            insert(s.management_audit).values(
                id=uuid4(),
                action=action,
                target_id=target,
            )
        )

    async def invitations(self, limit: int, offset: int) -> dict:
        columns = [c for c in s.invitation_codes.c if c.name != "code_hash"]
        async with self.database.sessions() as session:
            rows = await session.execute(
                select(*columns)
                .order_by(
                    s.invitation_codes.c.created_at.desc(),
                    s.invitation_codes.c.id,
                )
                .limit(limit)
                .offset(offset)
            )
            total = await session.scalar(select(func.count()).select_from(s.invitation_codes))
            return {"items": [dict(r) for r in rows.mappings()], "total": total}

    async def create_invitation(self, values: dict) -> dict:
        code, identifier = new_invitation_code(), uuid4()
        async with self.database.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        insert(s.invitation_codes)
                        .values(
                            id=identifier,
                            code_hash=invitation_digest(code),
                            code_hint=code[-4:],
                            **values,
                        )
                        .returning(*[c for c in s.invitation_codes.c if c.name != "code_hash"])
                    )
                )
                .mappings()
                .one()
            )
            await self._audit(session, "invitation.create", identifier)
            return {"invitation": dict(row), "code": code}

    async def update_invitation(self, identifier: UUID, values: dict) -> dict:
        async with self.database.sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        select(s.invitation_codes)
                        .where(
                            s.invitation_codes.c.id == identifier,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ManagementNotFound
            maximum = values.get("max_uses", row["max_uses"])
            if maximum is not None and maximum < row["use_count"]:
                raise ManagementConflict("Usage limit cannot be lower than redeemed accounts")
            if values:
                await session.execute(
                    update(s.invitation_codes)
                    .where(
                        s.invitation_codes.c.id == identifier,
                    )
                    .values(**values)
                )
            await self._audit(session, "invitation.update", identifier)
            return {k: values.get(k, v) for k, v in row.items() if k != "code_hash"}

    async def revoke_invitation(self, identifier: UUID) -> None:
        async with self.database.sessions.begin() as session:
            changed = await session.scalar(
                update(s.invitation_codes)
                .where(
                    s.invitation_codes.c.id == identifier,
                )
                .values(
                    revoked_at=func.coalesce(
                        s.invitation_codes.c.revoked_at, func.current_timestamp()
                    )
                )
                .returning(s.invitation_codes.c.id)
            )
            if changed is None:
                raise ManagementNotFound
            await self._audit(session, "invitation.revoke", identifier)

    async def users(self, limit: int, offset: int) -> dict:
        active_count = (
            select(func.count())
            .where(
                s.auth_sessions.c.user_id == s.users.c.id,
                s.auth_sessions.c.revoked_at.is_(None),
                s.auth_sessions.c.expires_at > func.current_timestamp(),
            )
            .correlate(s.users)
            .scalar_subquery()
        )
        query = (
            select(
                s.users.c.id,
                s.users.c.display_name,
                s.users.c.status,
                s.users.c.created_at,
                s.account_access.c.source.label("access_source"),
                s.account_access.c.expires_at.label("access_expires_at"),
                s.invitation_codes.c.label.label("invitation_label"),
                active_count.label("active_sessions"),
            )
            .outerjoin(
                s.account_access,
                s.account_access.c.user_id == s.users.c.id,
            )
            .outerjoin(
                s.invitation_codes, s.invitation_codes.c.id == s.account_access.c.invitation_id
            )
        )
        async with self.database.sessions() as session:
            rows = await session.execute(
                query.order_by(
                    s.users.c.created_at.desc(),
                    s.users.c.id,
                )
                .limit(limit)
                .offset(offset)
            )
            total = await session.scalar(select(func.count()).select_from(s.users))
            return {"items": [dict(r) for r in rows.mappings()], "total": total}

    async def update_user(self, identifier: UUID, values: dict) -> None:
        async with self.database.sessions.begin() as session:
            user = await session.scalar(
                select(s.users.c.id)
                .where(
                    s.users.c.id == identifier,
                )
                .with_for_update()
            )
            if user is None:
                raise ManagementNotFound
            access_values = {}
            if "status" in values:
                await session.execute(
                    update(s.users)
                    .where(s.users.c.id == identifier)
                    .values(status=values["status"])
                )
                access_values["revoked_at"] = (
                    func.current_timestamp() if values["status"] == "DEACTIVATED" else None
                )
            if "access_expires_at" in values:
                access_values["expires_at"] = values["access_expires_at"]
            if access_values:
                updated = await session.scalar(
                    update(s.account_access)
                    .where(
                        s.account_access.c.user_id == identifier,
                    )
                    .values(**access_values)
                    .returning(s.account_access.c.user_id)
                )
                if updated is None:
                    raise ManagementConflict("Account has no admission record")
            if values.get("access_expires_at") is not None:
                # Shortening access also shortens existing credential lifetimes.
                # Extending access never revives an expired/revoked session.
                await session.execute(
                    update(s.auth_sessions)
                    .where(s.auth_sessions.c.user_id == identifier)
                    .values(
                        expires_at=func.least(
                            s.auth_sessions.c.expires_at,
                            values["access_expires_at"],
                        )
                    )
                )
            if values.get("status") == "DEACTIVATED":
                await session.execute(
                    update(s.auth_sessions)
                    .where(
                        s.auth_sessions.c.user_id == identifier,
                        s.auth_sessions.c.revoked_at.is_(None),
                    )
                    .values(revoked_at=func.current_timestamp())
                )
            await self._audit(session, "user.update", identifier)

    async def sessions(self, user_id: UUID | None, limit: int, offset: int) -> dict:
        query = select(
            s.auth_sessions.c.id,
            s.auth_sessions.c.user_id,
            s.users.c.display_name,
            s.auth_sessions.c.created_at,
            s.auth_sessions.c.expires_at,
            s.auth_sessions.c.revoked_at,
        ).join(s.users)
        count = select(func.count()).select_from(s.auth_sessions)
        if user_id is not None:
            query = query.where(s.auth_sessions.c.user_id == user_id)
            count = count.where(s.auth_sessions.c.user_id == user_id)
        async with self.database.sessions() as session:
            rows = await session.execute(
                query.order_by(
                    s.auth_sessions.c.created_at.desc(),
                    s.auth_sessions.c.id,
                )
                .limit(limit)
                .offset(offset)
            )
            return {
                "items": [dict(r) for r in rows.mappings()],
                "total": await session.scalar(count),
            }

    async def revoke_sessions(
        self, *, session_id: UUID | None = None, user_id: UUID | None = None
    ) -> None:
        async with self.database.sessions.begin() as session:
            target = user_id or session_id
            if user_id is not None:
                exists = await session.scalar(select(s.users.c.id).where(s.users.c.id == user_id))
                predicate = s.auth_sessions.c.user_id == user_id
            else:
                exists = await session.scalar(
                    select(s.auth_sessions.c.id).where(
                        s.auth_sessions.c.id == session_id,
                    )
                )
                predicate = s.auth_sessions.c.id == session_id
            if exists is None:
                raise ManagementNotFound
            await session.execute(
                update(s.auth_sessions)
                .where(
                    predicate,
                    s.auth_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=func.current_timestamp())
            )
            await self._audit(session, "session.revoke", target)
