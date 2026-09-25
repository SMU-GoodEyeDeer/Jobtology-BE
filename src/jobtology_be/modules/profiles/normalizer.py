from dataclasses import dataclass
from typing import Protocol, final, override

from jobtology_be.contracts import CapabilityInput, NormalizedCapability, NormalizedProfile


class ProfileNormalizer(Protocol):
    """Resolve capability identities and experience codes, never planning constraints."""

    def normalize(
        self, user_id: str, profile_version: int, capabilities: list[CapabilityInput]
    ) -> NormalizedProfile: ...


@dataclass(frozen=True, slots=True)
class CapabilityCatalogEntry:
    entity_id: str
    aliases: frozenset[str]


@dataclass(frozen=True, slots=True)
class UnknownEntityIdError(Exception):
    entity_id: str

    @override
    def __str__(self) -> str:
        return f"unknown capability entity ID: {self.entity_id}"


@dataclass(frozen=True, slots=True)
class ConflictingEntityIdError(Exception):
    entity_id: str
    raw_text: str

    @override
    def __str__(self) -> str:
        return f"capability entity ID {self.entity_id} does not match raw text {self.raw_text!r}"


@dataclass(frozen=True, slots=True)
class UnknownExperienceCodeError(Exception):
    experience_code: str

    @override
    def __str__(self) -> str:
        return f"unknown experience code: {self.experience_code}"


@final
class CatalogProfileNormalizer:
    _aliases: dict[str, tuple[str, ...]]
    _entity_ids: frozenset[str]
    _allowed_experience_codes: frozenset[str]

    def __init__(
        self,
        entries: tuple[CapabilityCatalogEntry, ...],
        allowed_experience_codes: frozenset[str],
    ) -> None:
        alias_entity_ids: dict[str, set[str]] = {}
        for entry in entries:
            for alias in entry.aliases:
                alias_entity_ids.setdefault(_lookup_key(alias), set()).add(entry.entity_id)

        self._aliases = {
            alias: tuple(sorted(entity_ids)) for alias, entity_ids in alias_entity_ids.items()
        }
        self._entity_ids = frozenset(entry.entity_id for entry in entries)
        self._allowed_experience_codes = allowed_experience_codes

    def normalize(
        self, user_id: str, profile_version: int, capabilities: list[CapabilityInput]
    ) -> NormalizedProfile:
        return NormalizedProfile(
            user_id=user_id,
            profile_version=profile_version,
            capabilities=[self._normalize_capability(capability) for capability in capabilities],
        )

    def _normalize_capability(self, capability: CapabilityInput) -> NormalizedCapability:
        unknown_experience_code = next(
            (
                code
                for code in capability.experience_codes
                if code not in self._allowed_experience_codes
            ),
            None,
        )
        if unknown_experience_code is not None:
            raise UnknownExperienceCodeError(experience_code=unknown_experience_code)

        lookup_key = _lookup_key(capability.raw_text)
        candidates = self._aliases.get(lookup_key, ()) if lookup_key else ()
        if capability.entity_id is not None:
            if capability.entity_id not in self._entity_ids:
                raise UnknownEntityIdError(entity_id=capability.entity_id)
            if capability.entity_id not in candidates:
                raise ConflictingEntityIdError(
                    entity_id=capability.entity_id,
                    raw_text=capability.raw_text,
                )
            return NormalizedCapability(
                raw_text=capability.raw_text,
                entity_id=capability.entity_id,
                experience_codes=capability.experience_codes,
                resolution="RESOLVED",
            )

        if len(candidates) == 1:
            return NormalizedCapability(
                raw_text=capability.raw_text,
                entity_id=candidates[0],
                experience_codes=capability.experience_codes,
                resolution="RESOLVED",
            )
        if len(candidates) > 1:
            return NormalizedCapability(
                raw_text=capability.raw_text,
                entity_id=None,
                experience_codes=capability.experience_codes,
                resolution="AMBIGUOUS",
            )
        return NormalizedCapability(
            raw_text=capability.raw_text,
            entity_id=None,
            experience_codes=capability.experience_codes,
            resolution="UNRESOLVED",
        )


def _lookup_key(value: str) -> str:
    return value.strip().casefold()
