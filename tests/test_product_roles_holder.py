import json
from pathlib import Path
from types import TracebackType
from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from jobtology_be.api.composition import lifespan
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.corpus.snapshot import (
    PublishedSnapshotSelection,
    PublishedSnapshotUnavailableError,
)
from jobtology_be.main import create_app
from jobtology_be.product_roles.builder import build_product_roles
from jobtology_be.product_roles.holder import ProductRoleHolder
from jobtology_be.product_roles.models import ProductRoleInputs, ProductRolePolicy
from jobtology_be.settings import Settings

POLICY = Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json"


def test_product_roles_holder_loads_snapshot_from_bound_readonly_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given a real engine with a narrow SQL-wire fake returning official source JSON
    calls: list[str] = []
    payload = json.dumps({
        "contract_version": "jobtology-product-role-inputs-v1",
        "sources": [{"source_id": "ncs_competency", "run_id": "ncs-1"}],
        "units": [{"code": "2001020211_24v1", "base_code": "2001020211", "name": "서버프로그램 구현",
                   "level": 4, "occupation_code": "20010202", "occupation_name": "Backend"}],
        "qualifications": [], "evidence": [],
    })

    class Result:
        def scalar_one(self) -> str:
            return payload

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool:
            return False

    class Connection(Transaction):
        def begin(self):
            return Transaction()

        async def execute(self, statement: TextClause, params=None) -> Result:
            calls.append(str(statement))
            if params is not None:
                assert "20010202" in params["codes"]
            return Result()

    monkeypatch.setattr(AsyncEngine, "connect", lambda _: Connection())
    from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
    catalog = PostgresSourceCatalog.create(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )
    approval_path = tmp_path / "artifact-approval.json"
    draft = build_product_roles(
        ProductRoleInputs.model_validate_json(payload),
        ProductRolePolicy.model_validate_json(POLICY.read_text()),
    )
    approval_path.write_text(json.dumps({
        "sha256": draft.digest, "approved_by": "synthetic test owner",
        "reviewed_at": "2026-10-05T09:00:00+00:00",
    }))
    holder = ProductRoleHolder(catalog.engine, POLICY, approval_path)

    async def exercise() -> None:
        try:
            assert holder.available is False
            assert await holder.load() is True
            snapshot = holder.snapshots[0]
            assert (await holder.get_snapshot(PublishedSnapshotSelection(
                "BACKEND_DEVELOPER", "product-roles-v1", snapshot.release.release_id,
            ))).occupation_id == "BACKEND_DEVELOPER"
            assert holder.display_name("BACKEND_DEVELOPER") == "백엔드 개발자"
        finally:
            await catalog.dispose()

    # When loading at startup, then the real snapshot model is accessible
    anyio.run(exercise)
    assert calls == [
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
        "SELECT CAST(catalog.product_role_inputs_v1(CAST(:codes AS text[])) AS text)",
    ]


def test_product_roles_holder_fails_closed_when_catalog_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an unavailable reader
    def timeout(self: AsyncEngine) -> None:
        raise TimeoutError

    monkeypatch.setattr(AsyncEngine, "connect", timeout)
    from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
    catalog = PostgresSourceCatalog.create(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )
    holder = ProductRoleHolder(catalog.engine, POLICY)

    async def exercise() -> None:
        try:
            # When startup loads, then the holder remains unavailable without aborting startup
            assert await holder.load() is False
            assert holder.available is False
            with pytest.raises(PublishedSnapshotUnavailableError):
                await holder.get_snapshot(PublishedSnapshotSelection(
                    "BACKEND_DEVELOPER", "product-roles-v1", "missing",
                ))
        finally:
            await catalog.dispose()

    anyio.run(exercise)


def test_holder_rejects_missing_and_changed_artifact_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given a mutable in-memory SQL source and initially absent artifact approval
    payload = [{"contract_version": "jobtology-product-role-inputs-v1",
                "sources": [{"source_id": "ncs_competency", "run_id": "ncs-1"}],
                "units": [{"code": "2001020211_24v1", "base_code": "2001020211",
                           "name": "서버프로그램 구현", "level": 4,
                           "occupation_code": "20010202", "occupation_name": "Backend"}],
                "qualifications": [], "evidence": []}]

    class Result:
        def scalar_one(self) -> str:
            return json.dumps(payload[0])

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> bool:
            return False

    class Connection(Transaction):
        def begin(self):
            return Transaction()

        async def execute(self, statement: TextClause, params=None) -> Result:
            return Result()

    monkeypatch.setattr(AsyncEngine, "connect", lambda _: Connection())
    from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
    catalog = PostgresSourceCatalog.create(
        "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    )
    approval_path = tmp_path / "artifact-approval.json"
    holder = ProductRoleHolder(catalog.engine, POLICY, approval_path)

    async def exercise() -> None:
        try:
            # When only policy rules exist, then the role draft cannot be read as published
            assert await holder.load() is False
            assert holder.available is False
            assert holder.draft is not None
            with pytest.raises(PublishedSnapshotUnavailableError):
                await holder.get_snapshot(PublishedSnapshotSelection(
                    "BACKEND_DEVELOPER", "product-roles-v1", "unapproved",
                ))
            approval_path.write_text(json.dumps({
                "sha256": holder.draft.digest, "approved_by": "synthetic test owner",
                "reviewed_at": "2026-10-05T09:00:00+00:00",
            }))
            assert await holder.load() is True
            old_release = holder.snapshots[0].release.release_id
            assert await holder.load() is True
            assert holder.snapshots[0].release.release_id == old_release
            payload[0]["sources"][0]["run_id"] = "ncs-2"
            # Then a changed source invalidates the exact approval and clears loaded state
            assert await holder.load() is False
            assert holder.available is False
            assert holder.display_name("BACKEND_DEVELOPER") is None
            assert holder.lookup("BACKEND_DEVELOPER:2001020211") is None
            with pytest.raises(PublishedSnapshotUnavailableError):
                await holder.get_snapshot(PublishedSnapshotSelection(
                    "BACKEND_DEVELOPER", "product-roles-v1", old_release,
                ))
            assert holder.draft is not None
            approval_path.write_text(json.dumps({
                "sha256": holder.draft.digest, "approved_by": "synthetic test owner",
                "reviewed_at": "2026-10-05T09:10:00+00:00",
            }))
            assert await holder.load() is True
            assert holder.snapshots[0].release.release_id != old_release
            with pytest.raises(PublishedSnapshotUnavailableError):
                await holder.get_snapshot(PublishedSnapshotSelection(
                    "BACKEND_DEVELOPER", "product-roles-v1", old_release,
                ))
        finally:
            await catalog.dispose()

    anyio.run(exercise)


def test_enabled_product_roles_fail_closed_without_ready_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a configured product DB and reader whose startup catalog call times out
    def timeout(self: AsyncEngine) -> None:
        raise TimeoutError

    monkeypatch.setattr(AsyncEngine, "connect", timeout)
    class Identity:
        async def current_principal(self) -> AuthenticatedPrincipal:
            return AuthenticatedPrincipal(user_id=uuid4())

    url = "postgresql+asyncpg://jobtology_catalog_reader@localhost/catalog"
    app = create_app(Settings(_env_file=None, database_url=url,
                              catalog_database_url=SecretStr(url), product_roles_enabled=True),
                     dependencies=ApiDependencies(identity_provider=Identity()))
    # When startup completes without a loaded role release, then reads/analysis remain unavailable
    with TestClient(app) as client:
        assert client.get("/api/v1/occupations").status_code == 503
        response = client.post("/api/v1/analyses", json={
            "goal_id": str(uuid4()), "expected_profile_version": 1, "basis_type": "EDITORIAL",
        })
    assert response.status_code == 503


def test_lifespan_starts_worker_only_after_successful_role_load() -> None:
    # Given a role loader and background worker with observable startup
    started = anyio.Event()
    class ReadyHolder:
        available = False

        async def load(self) -> bool:
            self.available = True
            return True

    class Worker:
        async def run(self) -> None:
            started.set()
            await anyio.sleep_forever()

    async def exercise() -> None:
        # When the app lifespan opens, then worker runs and closes by cancellation
        async with lifespan(FastAPI(), None, None, role_holder=ReadyHolder(),
                            inprocess_worker=Worker()):
            with anyio.fail_after(2):
                await started.wait()

    anyio.run(exercise)
