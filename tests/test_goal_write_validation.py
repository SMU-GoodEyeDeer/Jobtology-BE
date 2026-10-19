"""Goal writes require a usable catalog; retries and archival remain usable later."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.main import create_app
from jobtology_be.modules.goals import write_validation
from jobtology_be.settings import Settings

pytest_plugins = ("test_acceptance_persistence",)
USER_ID = UUID("d0b3d8d1-7de4-4a62-8c58-e652431377b4")
CATALOG = Path(__file__).parent / "fixtures/reviewed-goal-catalog.json"


class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=USER_ID)


def _payload(now: datetime | None = None) -> dict[str, int | str]:
    return {
        "expected_profile_version": 1, "goal_mode": "TARGETED",
        "occupation_id": "BACKEND_DEVELOPER",
        "target_by": ((now or datetime.now(UTC)) + timedelta(hours=1)).isoformat(),
        "timezone": "UTC", "original_time_phrase": "in one hour",
    }


async def _counts(database_url: str) -> tuple[int, ...]:
    database = Database.create(database_url)
    try:
        async with database.sessions() as session:
            return tuple([
                await session.scalar(select(func.count()).select_from(table))
                for table in (s.users, s.profiles, s.goals, s.user_state_events, s.recompute_requests)
            ])
    finally:
        await database.dispose()


@pytest.mark.parametrize("catalog_mode", ["missing", "product_roles_missing", "empty", "fixture"])
def test_missing_or_unavailable_catalog_rejects_targeted_goals_without_any_writes(
    acceptance_database_url: str, tmp_path: Path, catalog_mode: str,
) -> None:
    snapshot_path = None
    if catalog_mode in {"empty", "fixture", "product_roles_missing"}:
        snapshot_path = tmp_path / "catalog.json"
        document = json.loads(CATALOG.read_text())
        if catalog_mode == "empty":
            document["snapshots"] = []
        elif catalog_mode == "fixture":
            document["snapshots"][0]["release"]["state"] = "FIXTURE"
            document["snapshots"][0]["is_fixture"] = True
        snapshot_path.write_text(json.dumps(document))
    app = create_app(Settings(
        _env_file=None, database_url=acceptance_database_url,
        corpus_snapshot_path=snapshot_path, product_roles_enabled=catalog_mode == "product_roles_missing",
    ), dependencies=ApiDependencies(identity_provider=Identity()))
    with TestClient(app) as client:
        response = client.post("/api/v1/me/goals", json=_payload())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
    assert anyio.run(_counts, acceptance_database_url) == (0, 0, 0, 0, 0)


def test_reviewed_local_catalog_accepts_supported_roles_and_rejects_unknown_roles(
    acceptance_database_url: str,
) -> None:
    app = create_app(Settings(
        _env_file=None, database_url=acceptance_database_url, corpus_snapshot_path=CATALOG,
    ), dependencies=ApiDependencies(identity_provider=Identity()))
    with TestClient(app) as client:
        invalid = client.post("/api/v1/me/goals", json={**_payload(), "occupation_id": "UNKNOWN_ROLE"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["details"] == [
            {"location": ["body", "occupation_id"], "code": "unsupported_occupation"},
        ]
        assert anyio.run(_counts, acceptance_database_url) == (0, 0, 0, 0, 0)
        valid = client.post("/api/v1/me/goals", json=_payload())
    assert valid.status_code == 201
    assert anyio.run(_counts, acceptance_database_url)[:3] == (1, 1, 1)


def test_committed_goal_replay_and_archival_work_after_deadline_passes(
    acceptance_database_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [datetime.now(UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz is not None else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(write_validation, "datetime", Clock)
    from jobtology_be.infrastructure.persistence.contracts import UserCreate
    from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore

    async def provision_authenticated_user() -> None:
        database = Database.create(acceptance_database_url)
        try:
            await PostgresApplicationStore(database).create_user(UserCreate(user_id=USER_ID))
        finally:
            await database.dispose()

    anyio.run(provision_authenticated_user)
    app = create_app(Settings(
        _env_file=None, database_url=acceptance_database_url, corpus_snapshot_path=CATALOG,
    ), dependencies=ApiDependencies(identity_provider=Identity()))
    payload = _payload(clock[0])
    with TestClient(app) as client:
        first = client.post("/api/v1/me/goals", json=payload, headers={"Idempotency-Key": "one-hour-goal"})
        assert first.status_code == 201
        clock[0] += timedelta(hours=2)
        replay = client.post("/api/v1/me/goals", json=payload, headers={"Idempotency-Key": "one-hour-goal"})
        assert replay.status_code == 201
        assert replay.json() == first.json()
        new_write = client.post("/api/v1/me/goals", json=payload, headers={"Idempotency-Key": "new-goal"})
        assert new_write.status_code == 422
        assert new_write.json()["error"]["details"][0]["code"] == "deadline_not_future"
        archived = client.patch(f"/api/v1/me/goals/{first.json()['goal_id']}", json={
            **payload, "expected_profile_version": 2, "status": "ARCHIVED",
        })
        assert archived.status_code == 200
        assert archived.json()["status"] == "ARCHIVED"
    assert anyio.run(_counts, acceptance_database_url)[:3] == (1, 1, 1)
