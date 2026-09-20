from typing import Protocol

from jobtology_be.contracts import CapabilityInput, NormalizedProfile


class ProfileNormalizer(Protocol):
    """Resolve capability identities and experience codes, never planning constraints."""

    def normalize(
        self, user_id: str, profile_version: int, capabilities: list[CapabilityInput]
    ) -> NormalizedProfile: ...
