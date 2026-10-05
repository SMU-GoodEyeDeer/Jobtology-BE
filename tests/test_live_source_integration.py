"""Disposable restricted-reader live source feed through HTTP."""

import importlib
import json
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
from pydantic import SecretStr

from jobtology_be.api.analyses import require_analysis_service, require_requirement_metadata
from jobtology_be.api.dependencies import ApiDependencies
from jobtology_be.api.identity import AuthenticatedPrincipal
from jobtology_be.infrastructure.persistence.live_source_feed import PostgresLiveSourceFeed
from jobtology_be.infrastructure.persistence.source_catalog import PostgresSourceCatalog
from jobtology_be.main import create_app
from jobtology_be.product_roles.builder import build_product_roles
from jobtology_be.product_roles.models import ProductRoleInputs, ProductRolePolicy
from jobtology_be.settings import Settings

DB_TESTS = Path(__file__).resolve().parents[2] / "Jobtology-DB/hop/ontology/tests"


@dataclass(frozen=True, slots=True)
class Identity:
    async def current_principal(self) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(user_id=uuid4())


def test_live_feed_from_disposable_postgres_through_http(tmp_path: Path) -> None:
    # Given an isolated DB test harness and Docker daemon
    if not (DB_TESTS / "run.py").is_file():
        pytest.fail("Sibling Jobtology-DB fixture is required")
    try:
        daemon = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"Disposable PostgreSQL requires Docker: {type(exc).__name__}")
    if daemon.returncode:
        pytest.skip("Disposable PostgreSQL requires a running Docker daemon")

    sys.path.insert(0, str(DB_TESTS))
    try:
        db = importlib.import_module("run")
    finally:
        sys.path.remove(str(DB_TESTS))
    container = f"jobtology-live-be-{uuid4().hex}"
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
        old = {
            "kind": "JobPosting", "posting_id": "old", "title": "Old release only",
            "organization_code": "OLD", "date_posted": "2026-08-01",
        }
        db.sql("INSERT INTO ingestion.run(run_id,source_id,mode,policy_revision,state,created_at,completed_at) "
               "VALUES('old-job-alio','job_alio','FULL','fixture','READY',"
               "'2026-08-01T00:00:00Z','2026-08-01T00:01:00Z'); "
               "INSERT INTO ingestion.partition(run_id,partition_id,kind,page_size) "
               "VALUES('old-job-alio','all','FILE',1); "
               "INSERT INTO ingestion.document(run_id,document_id,partition_id,page_no,raw_path,raw_sha256,byte_length,encoding,http_status,retrieved_at,selected,verified_at) "
               "VALUES('old-job-alio','doc','all',1,'old.json',repeat('a',64),1,'UTF-8',200,"
               "'2026-08-01T00:00:30Z',true,now()); "
               "INSERT INTO ingestion.record(run_id,document_id,locator,source_record_id,source_payload,normalized) "
               f"VALUES('old-job-alio','doc','0','old',{db.js(old)},{db.js(old)})")
        db.load_sources({
            "job_alio": [
                {"kind": "JobPosting", "posting_id": "001", "representation": "list",
                 "title": "Engineer list", "organization_code": "C001", "organization_name": "Agency",
                 "date_posted": "2026-10-01", "closing_date": "2026-11-30",
                 "regions": "Seoul", "employment_type": "regular",
                 "ncs_category_codes": "2001", "ncs_category_names": "Software",
                 "eligibility_text": "PRIVATE_ELIGIBILITY"},
                {"kind": "JobPosting", "posting_id": "001", "representation": "detail",
                 "title": "Engineer detail", "organization_code": "C001", "organization_name": "Agency",
                 "date_posted": "2026-10-01", "closing_date": "2026-11-30",
                 "regions": "Seoul", "employment_type": "regular",
                 "ncs_category_codes": "2001", "ncs_category_names": "Software",
                 "source_url": "https://example.org/jobs/001", "headcount": 2,
                 "preference_text": "PRIVATE_PREFERENCE"},
                {"kind": "JobPosting", "posting_id": "002", "representation": "list",
                 "title": "Analyst", "organization_code": "C002",
                 "date_posted": "2026-10-02", "closing_date": "2026-10-10",
                 "regions": "Busan", "employment_type": "contract",
                 "disqualification_text": "PRIVATE_DISQUALIFICATION"},
                {"kind": "JobPosting", "posting_id": "002", "representation": "detail",
                 "title": "Analyst", "organization_code": "C002",
                 "date_posted": "2026-10-02", "closing_date": "2026-10-10",
                 "regions": "Busan", "employment_type": "contract"},
            ],
            "qnet_schedule": [
                {"kind": "ExamSession", "qualification_code": "T5H0", "year": 2026,
                 "round": 1, "category_code": "C", "name": "First exam",
                 "dates": {"docRegStartDt": "2026-10-01", "docRegEndDt": "2026-10-07"}},
                {"kind": "ExamSession", "qualification_code": "T5H0", "year": 2026,
                 "round": 2, "category_code": "C", "name": "Second exam",
                 "dates": {"docRegStartDt": "2026-12-01", "docRegEndDt": "2026-12-07"}},
            ],
            "ncs_qualification": [
                {"kind": "QualificationMapping", "competency_code": "2001020101_24v2",
                 "qualification_code": "T5H0", "qualification_name": "IT qualification",
                 "standard_version": "24V2", "unit_type": "MAND",
                 "minimum_training_hours": 40, "total_training_hours": 410,
                 "examining_organization": "Agency"},
            ],
            "ncs_competency": [
                {"kind": "Competency", "code": "2001020101_24v1", "name": "데이터베이스 설계",
                 "level": 4, "occupation_code": "20010201",
                 "occupation_name": "정보기술개발"},
                {"kind": "Competency", "code": "2001020211_24v1", "name": "서버프로그램 구현",
                 "level": 4, "occupation_code": "20010202",
                 "occupation_name": "응용SW엔지니어링"},
            ],
        })
        link_payload = db.js({
            "source_id": "job_alio", "source_posting_id": "001", "name": "Engineer detail",
            "extraction_reviewer": "PRIVATE_REVIEWER",
            "links": [{"competency_code": "2001020101_24v1", "reviewer_kind": "human",
                       "review_notes": "PRIVATE_NOTES", "reason": "PRIVATE_REASON",
                       "decision_id": "PRIVATE_DECISION", "duty": {"text": "설계", "position": "개발자"}}],
        })
        db.sql("INSERT INTO enrichment.link_publication"
               "(publication_id,source_hash,job_run_id,ncs_run_id,state,created_at) "
               "VALUES('be-link',repeat('a',64),'fixture-job_alio',"
               "'fixture-ncs_competency','READY','2026-10-05'); "
               "INSERT INTO enrichment.link_publication_source_run"
               "(publication_id,source_id,run_id) "
               "VALUES('be-link','job_alio','fixture-job_alio'); "
               "INSERT INTO enrichment.link_publication_item"
               "(publication_id,posting_id,enrichment_id,posting_identity,name,payload,payload_hash) "
               f"VALUES('be-link','001','reviewed:fixture','job_alio:001','Engineer detail',"
               f"{link_payload},enrichment.hash(({link_payload})::text))")
        db.sql((db.ROOT / "hop/ontology/sql/catalog_reader_grants.psql").read_text())
        db.sql(f"ALTER ROLE jobtology_catalog_reader PASSWORD {db.q(reader_password)}")

        address = db.cmd(["docker", "port", container, "5432/tcp"])
        port = int(address.rsplit(":", 1)[1])
        host_port = port
        if db.cmd(["docker", "context", "show"]) == "colima":
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
                assert await connection.fetchval("SELECT has_function_privilege(current_user, 'catalog.live_postings_v1(text,text,text,date,integer,integer)', 'EXECUTE')") is True
                assert await connection.fetchval("SELECT has_function_privilege(current_user, 'catalog.live_ncs_demand_v1(text,integer,integer)', 'EXECUTE')") is True
                assert await connection.fetchval("SELECT has_schema_privilege(current_user, 'ingestion', 'USAGE')") is False
                with pytest.raises(asyncpg.InsufficientPrivilegeError):
                    await connection.fetchval("SELECT count(*) FROM ingestion.record")
            finally:
                await connection.close()

        anyio.run(check_reader_privileges)
        catalog = PostgresSourceCatalog.create(url)
        feed = PostgresLiveSourceFeed(engine=catalog.engine)
        app = create_app(Settings(), dependencies=ApiDependencies(
            identity_provider=Identity(), source_catalog=catalog, live_source_feed=feed,
        ))
        # When an authenticated user browses the current source independently of catalog approval
        with TestClient(app) as client:
            try:
                listing = client.get("/api/v2/live/postings")
                filtered = client.get("/api/v2/live/postings", params={
                    "q": "Engineer", "region": "Seoul", "ncs_category": "2001",
                    "open_on": "2026-10-05",
                })
                page = client.get("/api/v2/live/postings", params={"limit": 1, "offset": 1})
                detail = client.get("/api/v2/live/postings/001")
                absent = client.get("/api/v2/live/postings/missing")
                exams = client.get("/api/v2/live/exam-sessions", params={
                    "qualification": "T5H0", "from": "2026-10-01", "to": "2026-10-31",
                })
                demand = client.get("/api/v2/live/ncs-demand", params={"ncs_prefix": "20010201"})
                invalid_demand = client.get("/api/v2/live/ncs-demand", params={"ncs_prefix": "20%"})
                # Then only the latest READY feed and safe declared fields are returned
                assert [r.status_code for r in (listing, filtered, page, detail, exams, demand)] == [200] * 6
                assert invalid_demand.status_code == 422
                assert absent.status_code == 404
                assert listing.json()["contract_version"] == "hop-live-source-v1"
                assert listing.json()["total"] == 2
                assert {item["posting_id"] for item in listing.json()["items"]} == {"001", "002"}
                assert filtered.json()["total"] == 1
                assert filtered.json()["items"][0]["title"] == "Engineer detail"
                assert page.json()["total"] == 2 and len(page.json()["items"]) == 1
                assert detail.json()["item"]["posting_id"] == "001"
                assert exams.json()["total"] == 1
                assert exams.json()["items"][0]["qualification_code"] == "T5H0"
                assert demand.json()["contract_version"] == "hop-live-ncs-demand-v1"
                assert [(source["publication_id"], source["posting_source"])
                        for source in demand.json()["sources"]] == [("be-link", "job_alio")]
                assert demand.json()["total"] == 1
                assert demand.json()["items"][0]["evidence"][0]["source_posting_id"] == "001"
                assert demand.json()["review"]["link_reviewer_kinds"] == {"human": 1}
                assert all(secret not in demand.text for secret in (
                    "PRIVATE_REVIEWER", "PRIVATE_NOTES", "PRIVATE_REASON", "PRIVATE_DECISION",
                ))
                for response in (listing, filtered, page, detail, exams):
                    assert all(term not in response.text.lower() for term in (
                        "eligibility", "preference", "selection", "disqualification",
                    ))
            finally:
                if client.portal is not None:
                    client.portal.call(catalog.dispose)
                    catalog = None

        catalog = PostgresSourceCatalog.create(url)
        role_settings = Settings(
            database_url=url, catalog_database_url=SecretStr(url),
            product_roles_enabled=True, corpus_source="neo4j_query_api",
            db_link=SecretStr("bolt://neo4j@localhost:7687"),
            db_password=SecretStr("not-used"),
        )
        unapproved_app = create_app(
            role_settings,
            dependencies=ApiDependencies(identity_provider=Identity(), source_catalog=catalog),
        )
        with TestClient(unapproved_app) as client:
            assert client.get("/api/v1/occupations").status_code == 503
            if client.portal is not None:
                client.portal.call(catalog.dispose)
                catalog = None

        policy = ProductRolePolicy.model_validate_json(
            (Path(__file__).resolve().parents[1] / "config/product_roles/policy.v1.json").read_text()
        )
        codes = ",".join(db.q(code) for code in policy.requested_occupation_codes)
        inputs = ProductRoleInputs.model_validate_json(db.sql(
            f"SELECT CAST(catalog.product_role_inputs_v1(ARRAY[{codes}]::text[]) AS text)"
        ))
        draft = build_product_roles(inputs, policy)
        approval_path = tmp_path / "synthetic-artifact-approval.json"
        approval_path.write_text(json.dumps({
            "sha256": draft.digest, "approved_by": "synthetic test owner",
            "reviewed_at": "2026-10-05T09:00:00+00:00",
        }))
        catalog = PostgresSourceCatalog.create(url)
        role_app = create_app(
            Settings(database_url=url, catalog_database_url=SecretStr(url),
                     product_roles_enabled=True, corpus_source="neo4j_query_api",
                     db_link=SecretStr("bolt://neo4j@localhost:7687"),
                     db_password=SecretStr("not-used"),
                     product_role_artifact_approval_path=approval_path),
            dependencies=ApiDependencies(identity_provider=Identity(), source_catalog=catalog),
        )
        with TestClient(role_app) as client:
            assert require_analysis_service in role_app.dependency_overrides
            metadata = role_app.dependency_overrides[require_requirement_metadata]().lookup(
                "BACKEND_DEVELOPER:2001020211"
            )
            assert metadata.ncs_level == 4
            assert metadata.estimated_hours == 20
            assert metadata.hours_basis == "ESTIMATED"
            occupations = client.get("/api/v1/occupations")
            assert occupations.status_code == 200
            assert "BACKEND_DEVELOPER" in {
                item["occupation_id"] for item in occupations.json()
            }
            assert next(item["name"] for item in occupations.json()
                        if item["occupation_id"] == "BACKEND_DEVELOPER") == "백엔드 개발자"
            assert next(item["release_id"] for item in occupations.json()
                        if item["occupation_id"] == "BACKEND_DEVELOPER") == (
                            f"product-roles-v1-{draft.digest}"
                        )
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
