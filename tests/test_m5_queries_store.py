from datetime import UTC, datetime

from jobtology_be.infrastructure.persistence.roadmap_source_validity import parse_source_validity


def test_source_validity_keeps_unspecified_sources_current() -> None:
    # Given / When
    validity = parse_source_validity({})

    # Then
    assert validity.source_is_valid
    assert validity.expires_at is None


def test_source_validity_rejects_invalid_or_malformed_metadata() -> None:
    # Given / When
    invalid_source = parse_source_validity({"source_is_valid": False})
    malformed_expiration = parse_source_validity({"expires_at": "not-a-timestamp"})

    # Then
    assert not invalid_source.source_is_valid
    assert not malformed_expiration.source_is_valid


def test_source_validity_parses_an_aware_expiration() -> None:
    # Given / When
    validity = parse_source_validity({"expires_at": "2026-09-23T09:00:00+09:00"})

    # Then
    assert validity.source_is_valid
    assert validity.expires_at == datetime(2026, 9, 23, tzinfo=UTC)
