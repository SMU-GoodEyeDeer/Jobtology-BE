from jobtology_be.modules.auth.session import (
    SessionCredentials,
    issue_session_credentials,
)


def test_issued_session_credentials_round_trip_and_hash_independently() -> None:
    # Given
    issued = issue_session_credentials()

    # When
    parsed = SessionCredentials.from_cookie_value(issued.cookie_value)
    hashes = issued.hashes

    # Then
    assert parsed == issued
    assert len(hashes.token_hash) == 32
    assert len(hashes.csrf_hash) == 32
    assert hashes.token_hash != hashes.csrf_hash


def test_session_credentials_reject_malformed_cookie_value() -> None:
    # Given / When
    parsed = SessionCredentials.from_cookie_value("not-a-paired-session")

    # Then
    assert parsed is None
