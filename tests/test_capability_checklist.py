from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_acceptance_m5_worker import CAPABILITY_ENTITY_ID, CAPABILITY_LABEL, _snapshot

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.capabilities import (
    CapabilityMutationCommand,
    CapabilityResult,
    CapabilityService,
    OnboardingCapabilitiesCommand,
)
from jobtology_be.infrastructure.persistence.contracts import OnboardingCapabilityUnit
from jobtology_be.main import create_app
from jobtology_be.product_roles.checklist import (
    ChecklistDocument,
    ChecklistOccupationUnavailableError,
    OnboardingChecklistCatalog,
    UnknownChecklistItemError,
    load_checklist_document,
)
from jobtology_be.settings import Settings

USER_ID = UUID("8f0c7b2a-3d4e-4f5a-9b6c-7d8e9f0a1b2c")
STALE_ENTITY_ID = "ncs:unit:retired"


def _document() -> ChecklistDocument:
    return ChecklistDocument.model_validate(
        {
            "version": 3,
            "occupations": {
                "BACKEND_DEVELOPER": {
                    "groups": [
                        {
                            "label": "서버",
                            "items": [
                                {"item_id": "B1", "label": "API를 만들어 봤어요",
                                 "entity_ids": [CAPABILITY_ENTITY_ID]},
                                {"item_id": "B2", "label": "API와 폐지 단위",
                                 "entity_ids": [CAPABILITY_ENTITY_ID, STALE_ENTITY_ID]},
                            ],
                        },
                        {
                            "label": "폐지",
                            "items": [
                                {"item_id": "B3", "label": "폐지 단위만",
                                 "entity_ids": [STALE_ENTITY_ID]},
                            ],
                        },
                    ]
                },
                "FRONTEND_DEVELOPER": {
                    "groups": [
                        {"label": "화면", "items": [
                            {"item_id": "F1", "label": "화면", "entity_ids": ["x"]},
                        ]},
                    ]
                },
            },
        }
    )


def _catalog() -> OnboardingChecklistCatalog:
    return OnboardingChecklistCatalog(document=_document(), snapshots=lambda: (_snapshot(),))


def test_checklist_hides_items_and_groups_without_current_requirement_links() -> None:
    checklist = _catalog().checklist("BACKEND_DEVELOPER")

    assert checklist.version == 3
    assert [group.label for group in checklist.groups] == ["서버"]
    assert [item.item_id for item in checklist.groups[0].items] == ["B1", "B2"]


def test_checklist_requires_both_a_definition_and_a_published_role_snapshot() -> None:
    with pytest.raises(ChecklistOccupationUnavailableError):
        _ = _catalog().checklist("FRONTEND_DEVELOPER")
    with pytest.raises(ChecklistOccupationUnavailableError):
        _ = _catalog().checklist("DATA_ANALYST")


def test_resolve_uses_requirement_labels_and_deduplicates_shared_units() -> None:
    units = _catalog().resolve("BACKEND_DEVELOPER", ("B2", "B1"))

    assert len(units) == 1
    assert units[0].entity_id == CAPABILITY_ENTITY_ID
    assert units[0].raw_text == CAPABILITY_LABEL
    assert units[0].item_id == "B2"


@pytest.mark.parametrize("item_id", ["B3", "missing", "F1"])
def test_resolve_rejects_unknown_hidden_or_foreign_items(item_id: str) -> None:
    with pytest.raises(UnknownChecklistItemError):
        _ = _catalog().resolve("BACKEND_DEVELOPER", (item_id,))


def test_packaged_checklist_document_is_valid_and_uses_unique_item_ids() -> None:
    document = load_checklist_document(
        Path(__file__).resolve().parents[1] / "config/product_roles/onboarding_checklist.v1.json"
    )
    item_ids = [
        item.item_id
        for occupation in document.occupations.values()
        for group in occupation.groups
        for item in group.items
    ]

    assert set(document.occupations) == {
        "AI_ENGINEER", "BACKEND_DEVELOPER", "DATA_ANALYST", "FRONTEND_DEVELOPER",
    }
    assert len(item_ids) == len(set(item_ids)) == 42


class StaticIdentityProvider:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


class RecordingOnboardingService(CapabilityService):
    command: OnboardingCapabilitiesCommand | None = None

    async def upsert(self, user_id: UUID, command: CapabilityMutationCommand) -> CapabilityResult:
        raise AssertionError("not used")

    async def delete(self, user_id: UUID, capability_id: UUID, expected_profile_version: int) -> int:
        raise AssertionError("not used")

    async def replace_onboarding(self, user_id: UUID, command: OnboardingCapabilitiesCommand) -> int:
        assert user_id == USER_ID
        self.command = command
        return command.expected_profile_version + 1


def _client(service: CapabilityService) -> TestClient:
    return TestClient(
        create_app(
            Settings(enable_fixtures=False),
            dependencies=ApiDependencies(
                identity_provider=StaticIdentityProvider(),
                capability_service=service,
                onboarding_checklist=_catalog(),
            ),
        )
    )


def test_checklist_endpoint_returns_visible_groups_without_unit_identifiers() -> None:
    with _client(RecordingOnboardingService()) as client:
        response = client.get("/api/v1/occupations/BACKEND_DEVELOPER/capability-checklist")
        missing = client.get("/api/v1/occupations/DATA_ANALYST/capability-checklist")

    assert response.status_code == 200
    assert response.json() == {
        "occupation_id": "BACKEND_DEVELOPER",
        "checklist_version": 3,
        "groups": [
            {
                "label": "서버",
                "items": [
                    {"item_id": "B1", "label": "API를 만들어 봤어요"},
                    {"item_id": "B2", "label": "API와 폐지 단위"},
                ],
            }
        ],
    }
    assert missing.status_code == 404


def test_onboarding_replace_resolves_items_into_one_service_call() -> None:
    service = RecordingOnboardingService()
    with _client(service) as client:
        response = client.put(
            "/api/v1/me/capabilities/onboarding",
            json={
                "expected_profile_version": 4,
                "occupation_id": "BACKEND_DEVELOPER",
                "item_ids": ["B1", "B2"],
            },
        )

    assert response.status_code == 200
    assert response.json() == {"profile_version": 5, "saved_count": 1}
    assert service.command == OnboardingCapabilitiesCommand(
        expected_profile_version=4,
        occupation_id="BACKEND_DEVELOPER",
        checklist_version=3,
        units=(OnboardingCapabilityUnit(item_id="B1", raw_text=CAPABILITY_LABEL),),
    )


def test_onboarding_replace_accepts_an_empty_answer_list() -> None:
    service = RecordingOnboardingService()
    with _client(service) as client:
        response = client.put(
            "/api/v1/me/capabilities/onboarding",
            json={"expected_profile_version": 2, "occupation_id": "BACKEND_DEVELOPER", "item_ids": []},
        )

    assert response.status_code == 200
    assert response.json() == {"profile_version": 3, "saved_count": 0}
    assert service.command is not None
    assert service.command.units == ()


def test_onboarding_replace_rejects_unknown_items_without_writing() -> None:
    service = RecordingOnboardingService()
    with _client(service) as client:
        unknown_item = client.put(
            "/api/v1/me/capabilities/onboarding",
            json={"expected_profile_version": 2, "occupation_id": "BACKEND_DEVELOPER",
                  "item_ids": ["B3"]},
        )
        unknown_occupation = client.put(
            "/api/v1/me/capabilities/onboarding",
            json={"expected_profile_version": 2, "occupation_id": "DATA_ANALYST", "item_ids": []},
        )

    assert unknown_item.status_code == 422
    assert unknown_occupation.status_code == 404
    assert service.command is None
