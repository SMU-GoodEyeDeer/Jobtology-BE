"""Real API/worker regressions for goals added or changed after onboarding."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.roadmaps import PersistentRoadmapService
from jobtology_be.corpus.local_snapshot import LocalJsonPublishedCorpusSnapshotReader
from jobtology_be.infrastructure.persistence import schema as s
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.product_roles.worker import InProcessRecomputeLoop
from jobtology_be.settings import Settings
from jobtology_be.workers.recompute import build_leased_recompute_worker

pytest_plugins = ("test_acceptance_persistence",)
BACKEND = "BACKEND_DEVELOPER"
FRONTEND = "FRONTEND_DEVELOPER"


class Identity:
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


class Names:
    def display_name(self, occupation_id: str) -> str:
        return occupation_id


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    snapshots = []
    for occupation, subject in ((BACKEND, "backend"), (FRONTEND, "frontend")):
        snapshots.append({
            "occupation_id": occupation, "basis_version": "reviewed-v1",
            "release": {"release_id": "goal-lifecycle-v1", "state": "PUBLISHED",
                        "reviewed_at": "2026-10-19T00:00:00+00:00"},
            "is_fixture": False,
            "capability_entries": [{"entity_id": f"skill:{subject}", "aliases": [subject]}],
            "allowed_experience_codes": [],
            "requirements": [{
                "requirement_key": subject, "label": subject, "necessity": "REQUIRED",
                "entity_id": f"skill:{subject}", "support_refs": [f"review:{subject}"],
            }],
            "templates": [{
                "action_id": f"study:{subject}", "revision": 1, "title": f"Study {subject}",
                "estimated_hours": 1, "outcome_requirement_keys": [subject],
                "completion_criteria": [f"Publish a {subject} artifact"],
                "support_refs": [f"review:{subject}"], "cost": {"kind": "KNOWN", "krw": 0},
                "is_foundational": False,
            }],
        })
    path = tmp_path / "two-published-roles.json"
    path.write_text(json.dumps({"snapshots": snapshots}))
    return path


def _app(database_url: str, catalog: Path):
    return create_app(Settings(
        _env_file=None, database_url=database_url, corpus_snapshot_path=catalog,
        capability_list_authoritative=True,
    ), dependencies=ApiDependencies(identity_provider=Identity(uuid4())))


def _goal_payload(version: int, *, occupation: str | None = BACKEND,
                  mode: str = "TARGETED", status: str = "ACTIVE") -> dict:
    return {
        "expected_profile_version": version, "goal_mode": mode, "occupation_id": occupation,
        "target_by": (datetime.now(UTC) + timedelta(days=30)).isoformat(), "timezone": "UTC",
        "original_time_phrase": "in one month", "status": status,
    }


def _version(client: TestClient) -> int:
    response = client.get("/api/v1/me/profile")
    assert response.status_code == 200
    return response.json()["profile_version"]


def _analyse(client: TestClient, goal_id: str) -> str:
    response = client.post("/api/v1/analyses", json={
        "goal_id": goal_id, "expected_profile_version": _version(client), "basis_type": "EDITORIAL",
    })
    assert response.status_code == 202, response.text
    return response.json()["recompute_request_id"]


async def _drain(database_url: str, catalog: Path) -> None:
    database = Database.create(database_url)
    store = PostgresApplicationStore(database, capability_list_authoritative=True)
    loop = InProcessRecomputeLoop(
        worker=build_leased_recompute_worker(
            store=store, snapshot_reader=LocalJsonPublishedCorpusSnapshotReader.from_path(catalog),
        ), database=database, roadmap_service=PersistentRoadmapService(store), holder=Names(),
    )
    try:
        # Edits can leave an earlier in-flight version alongside the newest one.
        for _ in range(4):
            await loop.process_once()
    finally:
        await database.dispose()


def _active_roadmap(client: TestClient) -> dict:
    response = client.get("/api/v1/roadmaps")
    assert response.status_code == 200
    active = [roadmap for roadmap in response.json()["items"] if roadmap["state"] == "ACTIVE"]
    assert len(active) == 1
    return active[0]


def _initial_roadmap(
    client: TestClient, database_url: str, catalog: Path, *, progress_state: str = "IN_PROGRESS",
) -> tuple[str, dict]:
    goal = client.post("/api/v1/me/goals", json=_goal_payload(1))
    assert goal.status_code == 201
    goal_id = goal.json()["goal_id"]
    _analyse(client, goal_id)
    anyio.run(_drain, database_url, catalog)
    roadmap = _active_roadmap(client)
    assert roadmap["steps"][0]["action_id"] == "study:backend"
    progress = client.patch(
        f"/api/v1/roadmaps/{roadmap['roadmap_id']}/steps/{roadmap['steps'][0]['step_id']}",
        json={"state": progress_state, "expected_profile_version": _version(client),
              "expected_roadmap_version": roadmap["roadmap_version"]},
    )
    assert progress.status_code == 200
    plan = client.put(f"/api/v1/roadmaps/{roadmap['roadmap_id']}/plan", json={
        "weekly_hours": 5, "rest_weeks": [], "order": [roadmap["steps"][0]["step_id"]],
        "hours": {roadmap["steps"][0]["step_id"]: 2},
    })
    assert plan.status_code == 200, plan.text
    return goal_id, _active_roadmap(client)


@pytest.mark.parametrize("scenario,status_code,error_code", [
    ("no_goal", 404, "NOT_FOUND"),
    ("foreign_goal", 404, "NOT_FOUND"),
    ("discovery", 409, "ACTIVE_TARGETED_GOAL_REQUIRED"),
    ("draft", 409, "ACTIVE_TARGETED_GOAL_REQUIRED"),
    ("archived", 409, "ACTIVE_TARGETED_GOAL_REQUIRED"),
])
def test_analysis_without_owned_active_targeted_goal_returns_actionable_error_without_queueing(
    acceptance_database_url: str, catalog_path: Path,
    scenario: str, status_code: int, error_code: str,
) -> None:
    app = _app(acceptance_database_url, catalog_path)
    goal_id = str(uuid4())
    with TestClient(app) as client:
        if scenario != "no_goal":
            payload = _goal_payload(
                1,
                mode="DISCOVERY" if scenario == "discovery" else "TARGETED",
                occupation=None if scenario == "discovery" else BACKEND,
                status={"draft": "DRAFT", "archived": "ARCHIVED"}.get(scenario, "ACTIVE"),
            )
            created = client.post("/api/v1/me/goals", json=payload)
            assert created.status_code == 201
            goal_id = created.json()["goal_id"]

    async def queue_counts() -> tuple[int, ...]:
        database = Database.create(acceptance_database_url)
        try:
            async with database.sessions() as session:
                return tuple([await session.scalar(select(func.count()).select_from(table))
                              for table in (s.user_state_events, s.recompute_requests, s.outbox_jobs)])
        finally:
            await database.dispose()

    requester = _app(acceptance_database_url, catalog_path) if scenario == "foreign_goal" else app
    with TestClient(requester) as client:
        if scenario in {"no_goal", "foreign_goal"}:
            saved = client.put("/api/v1/me/profile", json={
                "expected_profile_version": 1, "major_raw": "Computer science",
            })
            assert saved.status_code == 200
        version = _version(client)
        before = anyio.run(queue_counts)
        response = client.post("/api/v1/analyses", json={
            "goal_id": goal_id, "expected_profile_version": version, "basis_type": "EDITORIAL",
        })
        assert response.status_code == status_code, response.text
        error = response.json()["error"]
        assert error["code"] == error_code
        assert response.headers["x-request-id"] == error["request_id"]
        assert anyio.run(queue_counts) == before


@pytest.mark.parametrize("initial_discovery", [False, True])
def test_first_target_or_discovery_conversion_can_request_fresh_analysis(
    acceptance_database_url: str, catalog_path: Path, initial_discovery: bool,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        response = client.post("/api/v1/me/goals", json=_goal_payload(
            1, mode="DISCOVERY" if initial_discovery else "TARGETED",
            occupation=None if initial_discovery else BACKEND,
        ))
        assert response.status_code == 201
        goal_id = response.json()["goal_id"]
        if initial_discovery:
            updated = client.patch(f"/api/v1/me/goals/{goal_id}", json=_goal_payload(_version(client)))
            assert updated.status_code == 200
        request_id = _analyse(client, goal_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)
        assert client.get(f"/api/v1/recomputations/{request_id}").json()["state"] == "COMPLETED"
        assert _active_roadmap(client)["goal_id"] == goal_id


@pytest.mark.parametrize("progress_state", ["IN_PROGRESS", "COMPLETED"])
def test_changing_target_archives_old_role_without_losing_progress_or_customization(
    acceptance_database_url: str, catalog_path: Path, progress_state: str,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        goal_id, old = _initial_roadmap(
            client, acceptance_database_url, catalog_path, progress_state=progress_state,
        )
        old_plan = client.get(f"/api/v1/roadmaps/{old['roadmap_id']}/plan").json()
        changed = client.patch(f"/api/v1/me/goals/{goal_id}", json=_goal_payload(
            _version(client), occupation=FRONTEND,
        ))
        assert changed.status_code == 200
        archived = client.get(f"/api/v1/roadmaps/{old['roadmap_id']}").json()
        assert archived["state"] == "ARCHIVED"
        assert archived["roadmap_version"] == old["roadmap_version"] + 1
        assert archived["steps"] == old["steps"]
        assert client.get(f"/api/v1/roadmaps/{old['roadmap_id']}/plan").json() == old_plan
        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["active_roadmap_id"] is None
        stale_activation = client.patch(f"/api/v1/roadmaps/{old['roadmap_id']}", json={
            "operation": "ACTIVATE", "expected_profile_version": _version(client),
            "expected_roadmap_version": archived["roadmap_version"],
        })
        assert stale_activation.status_code == 409
        request_id = _analyse(client, goal_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)
        result = client.get(f"/api/v1/recomputations/{request_id}").json()
        assert result["state"] == "COMPLETED", result
        new = _active_roadmap(client)
        assert new["roadmap_id"] != old["roadmap_id"]
        assert new["steps"][0]["action_id"] == "study:frontend"
        assert client.get(f"/api/v1/roadmaps/{old['roadmap_id']}").json()["steps"] == old["steps"]


def test_deadline_only_edit_keeps_active_roadmap_progress_and_plan(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        goal_id, old = _initial_roadmap(client, acceptance_database_url, catalog_path)
        old_plan = client.get(f"/api/v1/roadmaps/{old['roadmap_id']}/plan").json()
        changed = client.patch(f"/api/v1/me/goals/{goal_id}", json={
            **_goal_payload(_version(client)),
            "target_by": (datetime.now(UTC) + timedelta(days=45)).isoformat(),
        })
        assert changed.status_code == 200
        _analyse(client, goal_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)
        assert _active_roadmap(client) == old
        new_plan = client.get(f"/api/v1/roadmaps/{old['roadmap_id']}/plan").json()
        # The current deadline/warnings may change; saved hours/order remain intact.
        for field in ("weekly_hours", "rest_weeks", "saved_at", "is_customized", "steps"):
            assert new_plan[field] == old_plan[field]


def test_additional_draft_keeps_current_context_and_requires_explicit_goal_switch(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        goal_id, old = _initial_roadmap(client, acceptance_database_url, catalog_path)
        draft_payload = _goal_payload(_version(client), occupation=FRONTEND, status="DRAFT")
        created = client.post("/api/v1/me/goals", json=draft_payload)
        assert created.status_code == 201
        draft_id = created.json()["goal_id"]
        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["goal_id"] == goal_id
        assert dashboard["recompute_state"] == "PENDING"
        anyio.run(_drain, acceptance_database_url, catalog_path)
        assert _active_roadmap(client) == old
        blocked = client.patch(f"/api/v1/me/goals/{draft_id}", json={
            **draft_payload, "status": "ACTIVE", "expected_profile_version": _version(client),
        })
        assert blocked.status_code == 409
        assert _active_roadmap(client) == old
        archived = client.patch(f"/api/v1/me/goals/{goal_id}", json=_goal_payload(
            _version(client), status="ARCHIVED",
        ))
        assert archived.status_code == 200
        assert client.get(f"/api/v1/roadmaps/{old['roadmap_id']}").json()["state"] == "ARCHIVED"
        activated = client.patch(f"/api/v1/me/goals/{draft_id}", json={
            **draft_payload, "status": "ACTIVE", "expected_profile_version": _version(client),
        })
        assert activated.status_code == 200
        _analyse(client, draft_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)
        assert _active_roadmap(client)["goal_id"] == draft_id


@pytest.mark.parametrize("identity", ["legacy_results", "missing", "conflicting"])
def test_reactivating_legacy_roadmap_requires_consistent_pinned_goal_identity(
    acceptance_database_url: str, catalog_path: Path, identity: str,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        _, old = _initial_roadmap(client, acceptance_database_url, catalog_path)
        archived = client.patch(f"/api/v1/roadmaps/{old['roadmap_id']}", json={
            "operation": "ARCHIVE", "expected_profile_version": _version(client),
            "expected_roadmap_version": old["roadmap_version"],
        })
        assert archived.status_code == 200

        async def change_identity() -> None:
            database = Database.create(acceptance_database_url)
            try:
                async with database.sessions.begin() as session:
                    row = (await session.execute(select(
                        s.analyses.c.id, s.analyses.c.input_snapshot, s.analyses.c.results,
                    ).join(s.route_proposals, s.route_proposals.c.analysis_id == s.analyses.c.id)
                        .where(s.route_proposals.c.id == UUID(old["proposal_id"])))).one()
                    snapshot = dict(row.input_snapshot)
                    snapshot["snapshot_selection"] = dict(snapshot["snapshot_selection"])
                    snapshot["snapshot_selection"].pop("occupation_id")
                    results = dict(row.results)
                    if identity == "missing":
                        results.pop("occupation_id")
                    elif identity == "conflicting":
                        snapshot["snapshot_selection"]["occupation_id"] = FRONTEND
                    await session.execute(update(s.analyses).where(s.analyses.c.id == row.id).values(
                        input_snapshot=snapshot, results=results,
                    ))
            finally:
                await database.dispose()

        anyio.run(change_identity)
        response = client.patch(f"/api/v1/roadmaps/{old['roadmap_id']}", json={
            "operation": "ACTIVATE", "expected_profile_version": _version(client),
            "expected_roadmap_version": archived.json()["roadmap_version"],
        })
        assert response.status_code == (200 if identity == "legacy_results" else 409)


def test_role_projection_still_rejects_unknown_explicit_capability_ids(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        created = client.post('/api/v1/me/goals', json=_goal_payload(1))
        assert created.status_code == 201
        invalid = client.post('/api/v1/me/capabilities', json={
            'expected_profile_version': _version(client), 'category': 'LANGUAGE_TOOL',
            'raw_text': 'backend', 'entity_id': 'unreviewed:unknown-skill',
        })
        assert invalid.status_code == 201
        request_id = _analyse(client, created.json()['goal_id'])
        anyio.run(_drain, acceptance_database_url, catalog_path)
        result = client.get(f'/api/v1/recomputations/{request_id}').json()
        assert result['state'] == 'FAILED'
        assert result['error_code'] == 'INVALID_INPUT'


def test_profile_can_clear_optional_education_fields_without_inventing_a_major(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        saved = client.put("/api/v1/me/profile", json={
            "expected_profile_version": 1, "major_raw": "Computer science",
            "major_concept_id": "computer-science", "year": 3,
            "enrollment_status": "ENROLLED", "expected_graduation_on": "2028-02-29",
        })
        assert saved.status_code == 200
        cleared = client.put("/api/v1/me/profile", json={
            "expected_profile_version": saved.json()["profile_version"], "major_raw": "",
            "major_concept_id": None, "year": None,
            "enrollment_status": None, "expected_graduation_on": None,
        })
        assert cleared.status_code == 200, cleared.text
        profile = client.get("/api/v1/me/profile").json()
        assert profile["major_raw"] == ""
        assert profile["profile_version"] == cleared.json()["profile_version"]
        for field in ("major_concept_id", "year", "enrollment_status", "expected_graduation_on"):
            assert profile[field] is None
        stale = client.put("/api/v1/me/profile", json={
            "expected_profile_version": saved.json()["profile_version"], "major_raw": "stale",
        })
        assert stale.status_code == 409
        assert client.get("/api/v1/me/profile").json() == profile


def test_added_and_deleted_capability_persists_and_changes_current_analysis(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        created_goal = client.post("/api/v1/me/goals", json=_goal_payload(1))
        assert created_goal.status_code == 201
        goal_id = created_goal.json()["goal_id"]

        def analyze_status() -> str:
            request_id = _analyse(client, goal_id)
            anyio.run(_drain, acceptance_database_url, catalog_path)
            result = client.get(f"/api/v1/recomputations/{request_id}").json()
            assert result["state"] == "COMPLETED", result
            analysis = client.get(f"/api/v1/analyses/{result['analysis_id']}").json()
            return analysis["results"]["requirements"][0]["status"]

        assert analyze_status() == "UNMET"
        created = client.post("/api/v1/me/capabilities", json={
            "expected_profile_version": _version(client),
            "category": "LANGUAGE_TOOL", "raw_text": "backend",
        })
        assert created.status_code == 201
        capability_id = created.json()["capability_id"]
        detail_url = f"/api/v1/me/capabilities/{capability_id}"
        detail = client.get(detail_url).json()
        assert detail["raw_text"] == "backend"
        assert detail["category"] == "LANGUAGE_TOOL"
        assert detail["lifecycle"] == "ACTIVE"
        assert client.get("/api/v1/me/capabilities").json()["items"] == [detail]
        assert analyze_status() == "SATISFIED"

        deleted = client.request("DELETE", detail_url, json={
            "expected_profile_version": _version(client),
        })
        assert deleted.status_code == 200
        assert deleted.json()["profile_version"] == _version(client)
        assert client.get("/api/v1/me/capabilities").json()["items"] == []
        assert client.get(detail_url).status_code == 404
        assert analyze_status() == "UNMET"

        async def lifecycle() -> str:
            database = Database.create(acceptance_database_url)
            try:
                async with database.sessions() as session:
                    return await session.scalar(select(s.user_capabilities.c.lifecycle).where(
                        s.user_capabilities.c.id == UUID(capability_id),
                    ))
            finally:
                await database.dispose()

        assert anyio.run(lifecycle) == "REVOKED"


def test_timezone_edit_carries_local_calendar_when_analysis_reuses_pending_request(
    acceptance_database_url: str, catalog_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zoneinfo import ZoneInfo

    from jobtology_be.infrastructure.persistence import configured_recompute
    from jobtology_be.workers.context import RecomputeContextDocument

    now = datetime.now(UTC)
    # Sunday afternoon UTC is already Monday in Seoul, so the capacity week differs.
    sunday_utc = now.replace(hour=16, minute=15, second=0, microsecond=0) + timedelta(
        days=(6 - now.weekday()) % 7,
    )
    seoul = ZoneInfo("Asia/Seoul")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return sunday_utc.astimezone(tz) if tz is not None else sunday_utc.replace(tzinfo=None)

    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        created = client.post("/api/v1/me/goals", json=_goal_payload(1))
        assert created.status_code == 201
        goal_id = created.json()["goal_id"]
        _analyse(client, goal_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)
        monkeypatch.setattr(configured_recompute, "datetime", Clock)
        target = (now.astimezone(seoul) + timedelta(days=30)).replace(
            hour=23, minute=59, second=59, microsecond=0,
        )
        edited = client.patch(f"/api/v1/me/goals/{goal_id}", json={
            **_goal_payload(_version(client)), "timezone": "Asia/Seoul", "target_by": target.isoformat(),
        })
        assert edited.status_code == 200

        async def carried_request():
            database = Database.create(acceptance_database_url)
            try:
                async with database.sessions() as session:
                    return (await session.execute(select(
                        s.recompute_requests.c.id, s.recompute_contexts.c.payload,
                    ).join(s.recompute_contexts).where(
                        s.recompute_requests.c.profile_version == 3,
                        s.recompute_contexts.c.payload["goal_id"].astext == goal_id,
                    ))).one()
            finally:
                await database.dispose()

        pending = anyio.run(carried_request)
        request_id = _analyse(client, goal_id)
        assert request_id == str(pending.id)
        carried = RecomputeContextDocument.model_validate(pending.payload)
        assert carried.constraints.target_by == target
        assert carried.constraints.target_by.utcoffset() == timedelta(hours=9)
        assert carried.reference_at.utcoffset() == timedelta(hours=9)
        assert carried.planning_started_at.utcoffset() == timedelta(hours=9)
        first_slot = carried.calendar[0]
        assert first_slot.starts_at.utcoffset() == timedelta(hours=9)
        year, week, _ = sunday_utc.astimezone(seoul).isocalendar()
        assert first_slot.capacity_week_key == f"{year}-W{week:02d}"
        utc_year, utc_week, _ = sunday_utc.isocalendar()
        assert first_slot.capacity_week_key != f"{utc_year}-W{utc_week:02d}"


def test_invalid_legacy_goal_timezone_uses_context_failure_without_breaking_profile_edit(
    acceptance_database_url: str, catalog_path: Path,
) -> None:
    with TestClient(_app(acceptance_database_url, catalog_path)) as client:
        created = client.post("/api/v1/me/goals", json=_goal_payload(1))
        assert created.status_code == 201
        goal_id = created.json()["goal_id"]
        _analyse(client, goal_id)
        anyio.run(_drain, acceptance_database_url, catalog_path)

        async def set_legacy_timezone() -> None:
            database = Database.create(acceptance_database_url)
            try:
                async with database.sessions.begin() as session:
                    await session.execute(update(s.goals).where(s.goals.c.id == UUID(goal_id)).values(
                        timezone="Legacy/Invalid",
                    ))
            finally:
                await database.dispose()

        anyio.run(set_legacy_timezone)
        updated = client.put("/api/v1/me/profile", json={
            "expected_profile_version": _version(client), "major_raw": "Computer science",
        })
        assert updated.status_code == 200

        async def failed_request():
            database = Database.create(acceptance_database_url)
            try:
                async with database.sessions() as session:
                    return (await session.execute(select(
                        s.recompute_requests.c.state, s.recompute_requests.c.error_code,
                    ).where(s.recompute_requests.c.profile_version == 3))).one()
            finally:
                await database.dispose()

        assert anyio.run(failed_request) == ("FAILED", "MISSING_RECOMPUTE_CONTEXT")
