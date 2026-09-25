from starlette.responses import Response

from jobtology_be.modules.auth.session_cookies import (
    SessionCookiePolicy,
    clear_oauth_attempt_cookie,
    session_cookie_policy_context,
    set_oauth_attempt_cookie,
    set_session_cookie,
)


def test_session_cookie_is_host_only_httponly_and_lax() -> None:
    # Given
    response = Response()
    policy = SessionCookiePolicy(secure=True, session_ttl_seconds=3600)

    # When
    with session_cookie_policy_context(policy):
        set_session_cookie(response, "opaque-session")

    # Then
    header = response.headers["set-cookie"]
    assert "jobtology_session=opaque-session" in header
    assert "HttpOnly" in header
    assert "Path=/" in header
    assert "SameSite=lax" in header
    assert "Secure" in header
    assert "Domain=" not in header


def test_oauth_attempt_cookie_is_short_lived_and_cleared() -> None:
    # Given
    response = Response()
    policy = SessionCookiePolicy(secure=False, session_ttl_seconds=3600)

    # When
    with session_cookie_policy_context(policy):
        set_oauth_attempt_cookie(response, "browser-binding")
        clear_oauth_attempt_cookie(response)

    # Then
    headers = [value.decode("latin-1") for key, value in response.raw_headers if key == b"set-cookie"]
    assert "jobtology_oauth_attempt=browser-binding" in headers[0]
    assert "Max-Age=600" in headers[0]
    assert "HttpOnly" in headers[0]
    assert "SameSite=lax" in headers[0]
    assert "Domain=" not in headers[0]
    assert "jobtology_oauth_attempt=\"\"" in headers[1]
    assert "Max-Age=0" in headers[1]
