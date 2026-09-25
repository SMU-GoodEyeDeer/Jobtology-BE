import pytest

from jobtology_be.contracts import CapabilityInput
from jobtology_be.modules.profiles.normalizer import (
    CapabilityCatalogEntry,
    CatalogProfileNormalizer,
    ConflictingEntityIdError,
    UnknownEntityIdError,
    UnknownExperienceCodeError,
)


@pytest.fixture
def normalizer() -> CatalogProfileNormalizer:
    return CatalogProfileNormalizer(
        entries=(
            CapabilityCatalogEntry(
                entity_id="capability-sable",
                aliases=frozenset({"Sable", "Sable Script"}),
            ),
            CapabilityCatalogEntry(
                entity_id="capability-ember-one",
                aliases=frozenset({"Ember"}),
            ),
            CapabilityCatalogEntry(
                entity_id="capability-ember-two",
                aliases=frozenset({"ember"}),
            ),
        ),
        allowed_experience_codes=frozenset({"NOVICE", "PRACTICED"}),
    )


def test_normalize_returns_unresolved_for_blank_raw_text(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    raw_text = "   "
    capabilities = [CapabilityInput(raw_text=raw_text)]

    # When
    profile = normalizer.normalize("user-1", 1, capabilities)

    # Then
    assert profile.capabilities[0].model_dump() == {
        "raw_text": raw_text,
        "entity_id": None,
        "experience_codes": [],
        "resolution": "UNRESOLVED",
        "verification": "SELF_REPORTED",
    }


def test_normalize_resolves_exact_normalized_alias_without_changing_raw_text(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    raw_text = "  sAbLe ScRiPt  "
    capabilities = [CapabilityInput(raw_text=raw_text, experience_codes=["PRACTICED"])]

    # When
    profile = normalizer.normalize("user-1", 3, capabilities)

    # Then
    assert profile.model_dump() == {
        "user_id": "user-1",
        "profile_version": 3,
        "capabilities": [
            {
                "raw_text": raw_text,
                "entity_id": "capability-sable",
                "experience_codes": ["PRACTICED"],
                "resolution": "RESOLVED",
                "verification": "SELF_REPORTED",
            }
        ],
    }


def test_normalize_retains_unknown_raw_text_without_credit(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    raw_text = "unlisted capability"
    capabilities = [CapabilityInput(raw_text=raw_text, experience_codes=["NOVICE"])]

    # When
    profile = normalizer.normalize("user-1", 1, capabilities)

    # Then
    capability = profile.capabilities[0]
    assert capability.raw_text == raw_text
    assert capability.entity_id is None
    assert capability.resolution == "UNRESOLVED"


def test_normalize_marks_alias_collisions_ambiguous_without_explicit_id(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text=" ember ")]

    # When
    profile = normalizer.normalize("user-1", 1, capabilities)

    # Then
    capability = profile.capabilities[0]
    assert capability.entity_id is None
    assert capability.resolution == "AMBIGUOUS"


def test_normalize_resolves_collision_with_matching_explicit_id(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [
        CapabilityInput(raw_text="EMBER", entity_id="capability-ember-two", experience_codes=[])
    ]

    # When
    profile = normalizer.normalize("user-1", 1, capabilities)

    # Then
    capability = profile.capabilities[0]
    assert capability.entity_id == "capability-ember-two"
    assert capability.resolution == "RESOLVED"


def test_normalize_rejects_unknown_explicit_entity_id(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text="Sable", entity_id="capability-unknown")]

    # When / Then
    with pytest.raises(UnknownEntityIdError):
        _ = normalizer.normalize("user-1", 1, capabilities)


def test_normalize_rejects_explicit_entity_id_that_conflicts_with_raw_alias(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text="Sable", entity_id="capability-ember-one")]

    # When / Then
    with pytest.raises(ConflictingEntityIdError):
        _ = normalizer.normalize("user-1", 1, capabilities)


def test_normalize_rejects_explicit_entity_id_for_unknown_raw_text(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text="not in catalog", entity_id="capability-sable")]

    # When / Then
    with pytest.raises(ConflictingEntityIdError):
        _ = normalizer.normalize("user-1", 1, capabilities)


def test_normalize_rejects_experience_codes_outside_injected_catalog(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text="Sable", experience_codes=["EXPERT"])]

    # When / Then
    with pytest.raises(UnknownExperienceCodeError):
        _ = normalizer.normalize("user-1", 1, capabilities)


def test_normalize_is_deterministic_for_identical_catalog_and_input(
    normalizer: CatalogProfileNormalizer,
) -> None:
    # Given
    capabilities = [CapabilityInput(raw_text="ember")]

    # When
    first = normalizer.normalize("user-1", 2, capabilities)
    second = normalizer.normalize("user-1", 2, capabilities)

    # Then
    assert first == second
