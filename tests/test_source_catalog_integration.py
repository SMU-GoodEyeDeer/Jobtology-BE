"""Disposable DB-to-HTTP catalog contract; never connects to an existing database.

Run: uv run pytest -q -rs tests/test_source_catalog_integration.py
The graph-load row below is synthetic and is not native graph verification.
"""

import importlib
import secrets
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import anyio
import asyncpg
import pytest
from fastapi.testclient import TestClient

from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
from jobtology_be.main import create_app
from jobtology_be.settings import Settings

DB_TESTS = Path(__file__).resolve().parents[2] / "Jobtology-DB/hop/ontology/tests"
RELEASE = "catalog-be-fixture"
POSTING = "urn:jobtology:jobPosting:job_alio:001"


@dataclass(frozen=True, slots=True)
class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())


def test_catalog_from_disposable_postgres_through_http() -> None:
    # Given Docker and the sibling DB's synthetic six-source fixture
    if not (DB_TESTS / "run.py").is_file():
        pytest.fail("Sibling Jobtology-DB fixture is required")
    try:
        daemon = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Disposable PostgreSQL requires Docker: {type(exc).__name__}")
    if daemon.returncode:
        pytest.skip("Disposable PostgreSQL requires a running Docker daemon")

    # The DB fixture's helpers use run.PG; give this test exclusive ownership of
    # a random container and publish an ephemeral loopback port.
    sys.path.insert(0, str(DB_TESTS))
    try:
        db = importlib.import_module("run")
    finally:
        sys.path.remove(str(DB_TESTS))
    container = f"jobtology-catalog-be-{uuid4().hex}"
    admin_password = secrets.token_urlsafe(24)
    reader_password = secrets.token_urlsafe(24)
    db.__dict__["PG"] = container
    catalog = None
    tunnel = None
    try:
        db.cmd(["docker", "run", "-d", "--name", container, "-p", "127.0.0.1::5432",
                "-e", f"POSTGRES_PASSWORD={admin_password}", "-e", "POSTGRES_DB=ontologytest",
                "postgres:18-alpine"])
        db.boot()
        db.load_sources(db.fixture())
        db.sql("SELECT ontology.capture_posting_census_v1('fixture-job_alio')")
        db.sql(f"SELECT ontology.prepare_release('{RELEASE}'); "
               f"SELECT ontology.assemble_sources('{RELEASE}')")
        db.sql(f"SELECT ontology.bind_observations_v1('{RELEASE}')")
        db.sql(f"SELECT ontology.prepare_catalog_source_v1('{RELEASE}')")
        db.sql(f"SELECT ontology.seal_catalog_source_v1('{RELEASE}')")
        db.sql((db.ROOT / "hop/ontology/sql/catalog_reader_grants.psql").read_text())
        db.sql(f"ALTER ROLE jobtology_catalog_reader PASSWORD {db.q(reader_password)}")

        address = db.cmd(["docker", "port", container, "5432/tcp"])
        port = int(address.rsplit(":", 1)[1])
        host_port = port
        if db.cmd(["docker", "context", "show"]) == "colima":
            # Colima's VM-local loopback publication needs a host-side SSH tunnel.
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                host_port = reserved.getsockname()[1]
            tunnel = subprocess.Popen(
                ["ssh", "-F", str(Path.home() / ".colima/ssh_config"),
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
                    pytest.fail("Colima SSH tunnel exited before PostgreSQL became reachable")
                if time.monotonic() >= deadline:
                    pytest.fail("Docker published PostgreSQL port did not become reachable from host")
                threading.Event().wait(min(0.1, deadline - time.monotonic()))
        url = (f"postgresql+asyncpg://jobtology_catalog_reader:{reader_password}"
               f"@127.0.0.1:{host_port}/ontologytest")
        async def check_reader_privileges() -> None:
            connection = await asyncpg.connect(user="jobtology_catalog_reader",
                password=reader_password, host="127.0.0.1", port=host_port,
                database="ontologytest")
            try:
                assert await connection.fetchval("SELECT current_user") == "jobtology_catalog_reader"
                assert await connection.fetchval("SELECT has_schema_privilege(current_user, 'ontology', 'USAGE')") is False
                assert await connection.fetchval("SELECT has_function_privilege(current_user, 'catalog.catalog_summary_v1(text)', 'EXECUTE')") is True
                assert await connection.fetchval("SELECT has_function_privilege(current_user, 'catalog.approve_catalog_release(text,text,text,text)', 'EXECUTE')") is False
                assert await connection.fetchval("SELECT has_table_privilege(current_user, 'catalog.catalog_active_release', 'SELECT')") is False
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.fetchval("SELECT count(*) FROM ontology.revision")
            finally:
                await connection.close()

        anyio.run(check_reader_privileges)
        catalog = PostgresSourceCatalog.create(url)
        app = create_app(Settings(), dependencies=ApiDependencies(
            identity_provider=Identity(), source_catalog=catalog,
        ))
        with TestClient(app) as client:
            # When there is no approved pointer, then the reader and HTTP gate close.
            pending = client.get("/api/v2/catalog/summary")
            assert pending.status_code == 503
            assert pending.json()["error"]["code"] == "DATA_UNAVAILABLE"

            # A SQL fixture row exercises approval logic, not a real Neo4j load.
            db.sql(f"""INSERT INTO ontology.graph_load
                (load_id,release_id,database_id,manifest_hash,state,finished_at,node_count,edge_count)
                SELECT 'be-fixture-load','{RELEASE}','fixture-neo',manifest_hash,
                'VERIFIED',clock_timestamp(),(manifest->>'nodes')::bigint,
                (manifest->>'edges')::bigint FROM ontology.corpus_release
                WHERE release_id='{RELEASE}';
                UPDATE ontology.corpus_release SET graph_verified_at=clock_timestamp()
                WHERE release_id='{RELEASE}';
                SELECT catalog.approve_catalog_release('{RELEASE}','fixture-neo','test','source-only')""")

            # When the approved release is queried through the real reader,
            # then actual jsonb validates into response models, not fake payloads.
            async def check_models() -> None:
                direct = PostgresSourceCatalog.create(url)
                try:
                    summary = await direct.summary(RELEASE)
                    page = await direct.entities(RELEASE, "jobPosting", 1, 0)
                    detail = await direct.entity(RELEASE, POSTING)
                    relations = await direct.relations(RELEASE, POSTING, 20, 0)
                    assert summary.source_profile.analysis_available is False
                    assert summary.posting_selection_outcomes["SELECTION_PENDING"] == 1
                    assert page.items[0].entity_id == POSTING
                    assert "eligibility_text" not in detail.entity.source_facts
                    assert any(item.predicate == "POSTED_BY" for item in relations.items)
                finally:
                    await direct.dispose()

            anyio.run(check_models)
            summary = client.get("/api/v2/catalog/summary", params={"release_id": RELEASE})
            listing = client.get("/api/v2/catalog/postings", params={"release_id": RELEASE, "limit": 1})
            detail = client.get(f"/api/v2/catalog/entities/{POSTING}", params={"release_id": RELEASE})
            relations = client.get("/api/v2/catalog/relations", params={"release_id": RELEASE,
                                                                     "entity_id": POSTING})
            assert all(response.status_code == 200 for response in (summary, listing, detail, relations))
            assert summary.json()["release_id"] == RELEASE
            assert listing.json()["items"][0]["entity_id"] == POSTING
            assert detail.json()["entity"]["kind"] == "jobPosting"
            assert "eligibility_text" not in detail.text
            assert any(item["predicate"] == "POSTED_BY" for item in relations.json()["items"])
            assert client.get("/api/v2/catalog/entities/urn:missing", params={"release_id": RELEASE}).status_code == 404
            assert client.get("/api/v2/catalog/summary", params={"release_id": "other"}).status_code == 503
            assert client.get("/api/v2/catalog/summary", params={"preview": "true"}).status_code == 422

            # When a newer load starts, then existing approval is stale.
            db.sql(f"""INSERT INTO ontology.graph_load
                (load_id,release_id,database_id,manifest_hash,state)
                SELECT 'be-reload','{RELEASE}','fixture-neo',manifest_hash,'RUNNING'
                FROM ontology.corpus_release WHERE release_id='{RELEASE}'""")
            stale = client.get("/api/v2/catalog/summary")
            assert stale.status_code == 503
            assert stale.json()["error"]["code"] == "DATA_UNAVAILABLE"

            # When the release is revoked, then the closed gate is 410.
            db.sql(f"UPDATE ontology.corpus_release SET state='REVOKED', "
                   f"revoked_at=clock_timestamp() WHERE release_id='{RELEASE}'")
            revoked = client.get("/api/v2/catalog/summary")
            assert revoked.status_code == 410
            assert revoked.json()["error"]["code"] == "GONE"
            assert "CATALOG_" not in revoked.text
            if client.portal is not None:
                client.portal.call(catalog.dispose)
                catalog = None
    finally:
        if catalog is not None:
            anyio.run(catalog.dispose)
        if tunnel is not None:
            tunnel.terminate()
            tunnel.wait(timeout=5)
        subprocess.run(["docker", "rm", "-fv", container], capture_output=True, check=False)
