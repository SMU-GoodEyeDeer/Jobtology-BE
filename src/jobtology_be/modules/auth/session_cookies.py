from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_COOKIE_NAME: Final = "jobtology_session"
OAUTH_ATTEMPT_COOKIE_NAME: Final = "jobtology_oauth_attempt"
OAUTH_ATTEMPT_TTL_SECONDS: Final = 600


@dataclass(frozen=True, slots=True)
class SessionCookiePolicy:
    secure: bool
    session_ttl_seconds: int


_DEFAULT_COOKIE_POLICY: Final = SessionCookiePolicy(
    secure=True,
    session_ttl_seconds=60 * 60 * 24 * 7,
)
_CURRENT_COOKIE_POLICY: ContextVar[SessionCookiePolicy] = ContextVar(
    "jobtology_session_cookie_policy",
    default=_DEFAULT_COOKIE_POLICY,
)


class SessionCookiePolicyMiddleware:
    def __init__(self, app: ASGIApp, policy: SessionCookiePolicy) -> None:
        self._app = app
        self._policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        policy_token = _CURRENT_COOKIE_POLICY.set(self._policy)
        try:
            await self._app(scope, receive, send)
        finally:
            _CURRENT_COOKIE_POLICY.reset(policy_token)


@contextmanager
def session_cookie_policy_context(policy: SessionCookiePolicy) -> Iterator[None]:
    policy_token = _CURRENT_COOKIE_POLICY.set(policy)
    try:
        yield
    finally:
        _CURRENT_COOKIE_POLICY.reset(policy_token)


def set_session_cookie(response: Response, session_token: str) -> None:
    policy = _CURRENT_COOKIE_POLICY.get()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=policy.session_ttl_seconds,
        httponly=True,
        secure=policy.secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    policy = _CURRENT_COOKIE_POLICY.get()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=policy.secure,
        samesite="lax",
        path="/",
    )


def set_oauth_attempt_cookie(response: Response, browser_binding: str) -> None:
    policy = _CURRENT_COOKIE_POLICY.get()
    response.set_cookie(
        key=OAUTH_ATTEMPT_COOKIE_NAME,
        value=browser_binding,
        max_age=OAUTH_ATTEMPT_TTL_SECONDS,
        httponly=True,
        secure=policy.secure,
        samesite="lax",
        path="/",
    )


def clear_oauth_attempt_cookie(response: Response) -> None:
    policy = _CURRENT_COOKIE_POLICY.get()
    response.delete_cookie(
        key=OAUTH_ATTEMPT_COOKIE_NAME,
        httponly=True,
        secure=policy.secure,
        samesite="lax",
        path="/",
    )
