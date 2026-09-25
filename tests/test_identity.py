from uuid import UUID

from jobtology_be.api.identity import AuthenticatedPrincipal


def test_authenticated_principal_parses_user_id_as_uuid() -> None:
    # Given
    user_id = "d0b3d8d1-7de4-4a62-8c58-e652431377b4"

    # When
    principal = AuthenticatedPrincipal(user_id=user_id)

    # Then
    assert principal.user_id == UUID(user_id)
