import json
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_acceptance_m5_lifecycle import _drop_database, _provision_database, _upgrade_database
from test_acceptance_m5_worker import enqueue_recompute

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.application.services.analyses import AnalysisRequestCommand
from jobtology_be.application.services.analysis_inputs import PostgresAnalysisContextInputSource
from jobtology_be.application.services.roadmaps import PersistentRoadmapService
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.schema import recompute_contexts, roadmaps
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.main import create_app
from jobtology_be.product_roles.builder import build_product_roles
from jobtology_be.product_roles.models import ArtifactApproval, ProductRoleInputs, ProductRolePolicy
from jobtology_be.product_roles.worker import InProcessRecomputeLoop
from jobtology_be.settings import Settings
from jobtology_be.workers.recompute import build_leased_recompute_worker


@pytest.fixture
def acceptance_database_url() -> Iterator[str]:
    # Given a unique PostgreSQL container with an isolated product database
    try:
        daemon = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Disposable PostgreSQL requires Docker: {type(exc).__name__}")
    if daemon.returncode:
        pytest.skip("Disposable PostgreSQL requires a running Docker daemon")
    container = f"jobtology-role-worker-{uuid4().hex}"
    password = secrets.token_urlsafe(24)
    tunnel = None
    database = None
    try:
        subprocess.run(["docker", "run", "-d", "--name", container, "-p", "127.0.0.1::5432",
                        "-e", f"POSTGRES_PASSWORD={password}", "postgres:18-alpine"],
                       check=True, capture_output=True)
        deadline = time.monotonic() + 20
        while subprocess.run(["docker", "exec", container, "pg_isready", "-h", "127.0.0.1",
                              "-U", "postgres"], capture_output=True, check=False).returncode:
            if time.monotonic() >= deadline:
                pytest.fail("Disposable PostgreSQL did not become ready")
            threading.Event().wait(0.1)
        address = subprocess.run(["docker", "port", container, "5432/tcp"],
                                 check=True, capture_output=True, text=True).stdout.strip()
        port = int(address.rsplit(":", 1)[1])
        host_port = port
        context = subprocess.run(["docker", "context", "show"], check=True,
                                 capture_output=True, text=True).stdout.strip()
        if context == "colima":
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                host_port = reserved.getsockname()[1]
            tunnel = subprocess.Popen(
                ["ssh", "-F", str(Path.home() / ".colima/ssh_config"),
                 "-o", "ControlMaster=no", "-o", "ControlPath=none",
                 "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                 f"127.0.0.1:{host_port}:127.0.0.1:{port}", "colima"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        deadline = time.monotonic() + 15
        while True:
            try:
                with socket.create_connection(("127.0.0.1", host_port), timeout=1):
                    break
            except OSError:
                if tunnel is not None and tunnel.poll() is not None:
                    pytest.fail("Colima tunnel exited before PostgreSQL became reachable")
                if time.monotonic() >= deadline:
                    pytest.fail("Disposable PostgreSQL port was unreachable")
                threading.Event().wait(0.1)
        base_url = f"postgresql+asyncpg://postgres:{password}@127.0.0.1:{host_port}/postgres"
        database = anyio.run(_provision_database, base_url)
        _upgrade_database(database.database_url)
        yield database.database_url
    finally:
        if database is not None:
            anyio.run(_drop_database, database)
        if tunnel is not None:
            tunnel.terminate()
            tunnel.wait(timeout=5)
        subprocess.run(["docker", "rm", "-fv", container], capture_output=True, check=False)


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: UUID

    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=self.user_id)


@dataclass(frozen=True, slots=True)
class Names:
    def display_name(self, occupation_id: str) -> str | None:
        return "백엔드 개발자" if occupation_id == "BACKEND_DEVELOPER" else None


def test_ready_feasible_recompute_creates_one_active_roadmap(
    acceptance_database_url: str,
) -> None:
    # Given a real disposable product DB, active goal, and finalized feasible proposal
    user_id = uuid4()
    app = create_app(
        Settings(_env_file=None, database_url=acceptance_database_url),
        dependencies=ApiDependencies(identity_provider=Identity(user_id)),
    )
    with TestClient(app) as client:
        goal_response = client.post("/api/v1/me/goals", json={
            "expected_profile_version": 1, "goal_mode": "TARGETED",
            "occupation_id": "BACKEND_DEVELOPER", "target_by": "2026-12-31T00:00:00+00:00",
            "timezone": "UTC", "original_time_phrase": "by year end",
        })
        assert goal_response.status_code == 201
    goal_id = UUID(goal_response.json()["goal_id"])
    request_id = anyio.run(enqueue_recompute, acceptance_database_url, user_id, 2, goal_id)
    policy = ProductRolePolicy.model_validate_json(
        (Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json").read_text()
    )
    draft = build_product_roles(ProductRoleInputs.model_validate_json(json.dumps({
        "contract_version": "jobtology-product-role-inputs-v1",
        "sources": [{"source_id": "ncs_competency", "run_id": "ncs-ready"}],
        "units": [{"code": "2001020211_24v1", "base_code": "2001020211",
                   "name": "서버프로그램 구현", "level": 4,
                   "occupation_code": "20010202", "occupation_name": "Backend"}],
        "qualifications": [{"competency_code": "2001020211_24v1",
                            "qualification_code": "Q1", "qualification_name": "Q",
                            "minimum_training_hours": 1, "total_training_hours": 1}],
        "evidence": [],
    })), policy)
    built = draft.publish(ArtifactApproval(
        sha256=draft.digest, approved_by="synthetic test owner",
        reviewed_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
    ))

    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        store = PostgresApplicationStore(database)
        loop = InProcessRecomputeLoop(
            worker=build_leased_recompute_worker(store=store, snapshot_reader=built.reader),
            database=database,
            roadmap_service=PersistentRoadmapService(store),
            holder=Names(),
        )
        try:
            async with database.sessions.begin() as session:
                payload = await session.scalar(select(recompute_contexts.c.payload).where(
                    recompute_contexts.c.request_id == request_id,
                ))
                assert payload is not None
                payload["snapshot_selection"] = {
                    "occupation_id": "BACKEND_DEVELOPER", "basis_version": "product-roles-v1",
                    "release_id": built.reader.snapshots[0].release.release_id,
                    "source": "local_json",
                }
                await session.execute(update(recompute_contexts).where(
                    recompute_contexts.c.request_id == request_id,
                ).values(payload=payload))
            # When the in-process cycle runs twice, then a generated role snapshot yields one ACTIVE roadmap
            await loop.process_once()
            await loop.process_once()
            async with database.sessions() as session:
                rows = (await session.execute(select(
                    roadmaps.c.title, roadmaps.c.state, roadmaps.c.profile_version,
                ).where(roadmaps.c.goal_id == goal_id))).all()
            assert rows == [("백엔드 개발자 로드맵", "ACTIVE", 2)]
        finally:
            await database.dispose()

    anyio.run(exercise)


def test_authoritative_empty_capabilities_are_complete(acceptance_database_url: str) -> None:
    # Given a real profile, goal and preferences without any stored capabilities
    user_id = uuid4()
    app = create_app(
        Settings(_env_file=None, database_url=acceptance_database_url),
        dependencies=ApiDependencies(identity_provider=Identity(user_id)),
    )
    with TestClient(app) as client:
        goal = client.post("/api/v1/me/goals", json={
            "expected_profile_version": 1, "goal_mode": "TARGETED",
            "occupation_id": "BACKEND_DEVELOPER", "target_by": "2026-12-31T00:00:00+00:00",
            "timezone": "UTC", "original_time_phrase": "by year end",
        })
        assert goal.status_code == 201
        preferences = client.put("/api/v1/me/route-preferences", json={
            "expected_profile_version": 2, "available_hours_per_week": 4,
            "availability_source": "FLEXIBLE_WEEKLY", "budget_mode": "REGULAR",
            "max_out_of_pocket_krw": None, "fastest_path": False,
            "needs_portfolio": False, "career_switch": False,
        })
        assert preferences.status_code == 200

    async def exercise() -> None:
        database = Database.create(acceptance_database_url)
        command = AnalysisRequestCommand(
            goal_id=UUID(goal.json()["goal_id"]), expected_profile_version=3,
            basis_type="EDITORIAL",
        )
        reference = datetime(2026, 10, 5, tzinfo=UTC)
        try:
            # When the authoritative flag is enabled, then zero entries prove unmatched units UNMET
            default = await PostgresAnalysisContextInputSource(database).load_context_inputs(
                user_id, command, reference,
            )
            authoritative = await PostgresAnalysisContextInputSource(
                database, capability_list_authoritative=True,
            ).load_context_inputs(user_id, command, reference)
            assert default.completeness.entities_complete is False
            assert authoritative.completeness.entities_complete is True
            assert authoritative.capabilities == ()
        finally:
            await database.dispose()

    anyio.run(exercise)
