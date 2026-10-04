"""Opt-in bootstrap for the session endpoint, never for product identity resolution."""

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from jobtology_be.api.auth_session import SessionResponse
from jobtology_be.api.identity import SessionIdentityProvider
from jobtology_be.infrastructure.persistence.auth_contracts import GuestSessionUnavailableError
from jobtology_be.modules.auth.session import GuestSessionStore, issue_session_credentials
from jobtology_be.modules.auth.session_cookies import set_session_cookie
from jobtology_be.settings import Settings


@dataclass(frozen=True, slots=True)
class GuestSessionBootstrap:
    store: GuestSessionStore
    settings: Settings

    async def current_session(self, request: Request, response: Response) -> SessionResponse:
        origin = request.headers.get("origin")
        if (
            origin is not None and origin not in self.settings.cors_origins
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, headers={"Cache-Control": "no-store"})
        provider = SessionIdentityProvider(self.store, frozenset(self.settings.cors_origins))
        try:
            try:
                session = await provider.current_session(request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
            else:
                version = await self.store.read_profile_version(session.principal.user_id)
                if version is None:
                    raise GuestSessionUnavailableError
                return SessionResponse(
                    user_id=session.principal.user_id,
                    csrf_token=session.csrf_token,
                    profile_version=version,
                )
            credentials = issue_session_credentials()
            issued = await self.store.create_guest_session(
                credentials.session_issue(self.settings.session_lifetime),
                self.settings.guest_session_max_new_per_minute,
            )
            if issued is None:
                raise HTTPException(429, headers={"Cache-Control": "no-store", "Retry-After": "60"})
        except (SQLAlchemyError, GuestSessionUnavailableError):
            raise HTTPException(503, headers={"Cache-Control": "no-store"}) from None
        set_session_cookie(response, credentials.cookie_value)
        return SessionResponse(
            user_id=issued.user_id,
            csrf_token=credentials.csrf_token,
            profile_version=issued.profile_version,
        )
