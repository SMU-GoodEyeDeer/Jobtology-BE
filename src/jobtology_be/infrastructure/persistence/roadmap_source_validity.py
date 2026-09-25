from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from jobtology_be.infrastructure.persistence.contracts import JsonValue


@dataclass(frozen=True, slots=True)
class SourceValidity:
    source_is_valid: bool
    expires_at: datetime | None


def parse_source_validity(validity: Mapping[str, JsonValue]) -> SourceValidity:
    source_is_valid = validity.get("source_is_valid", True)
    if not isinstance(source_is_valid, bool):
        return SourceValidity(source_is_valid=False, expires_at=None)
    serialized_expiration = validity.get("expires_at")
    if serialized_expiration is None:
        return SourceValidity(source_is_valid=source_is_valid, expires_at=None)
    if not isinstance(serialized_expiration, str):
        return SourceValidity(source_is_valid=False, expires_at=None)
    try:
        parsed = datetime.fromisoformat(serialized_expiration)
    except ValueError:
        return SourceValidity(source_is_valid=False, expires_at=None)
    if parsed.tzinfo is None:
        return SourceValidity(source_is_valid=False, expires_at=None)
    return SourceValidity(
        source_is_valid=source_is_valid,
        expires_at=parsed.astimezone(UTC),
    )


def source_is_current(source_validity: SourceValidity, reference_at: datetime) -> bool:
    return source_validity.source_is_valid and (
        source_validity.expires_at is None
        or source_validity.expires_at > reference_at.astimezone(UTC)
    )
