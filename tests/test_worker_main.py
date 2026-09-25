import json
from dataclasses import dataclass
from os import environ
from pathlib import Path
from subprocess import run
from sys import executable

import pytest
from pydantic import SecretStr

from jobtology_be.workers.main import WorkerSettings, run_once

pytest_plugins = ("test_acceptance_m5_lifecycle",)


@dataclass(slots=True)
class RecordingCorpusSource:
    legacy_snapshot_reader: object
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


@dataclass(slots=True)
class RecordingLeasedWorker:
    limits: list[int]

    async def process_once(self, *, limit: int) -> tuple[object, ...]:
        self.limits.append(limit)
        return ()


def test_worker_settings_require_explicit_database_and_snapshot_configuration(
    monkeypatch,
) -> None:
    # Given
    snapshot_path = Path("/tmp/published-corpus.json")
    monkeypatch.setenv("JOBTOLOGY_DATABASE_URL", "postgresql+asyncpg://worker@127.0.0.1:5432/jobtology")
    monkeypatch.setenv("JOBTOLOGY_CORPUS_SNAPSHOT_PATH", str(snapshot_path))

    # When
    settings = WorkerSettings.model_validate({})

    # Then
    assert settings.database_url == "postgresql+asyncpg://worker@127.0.0.1:5432/jobtology"
    assert settings.corpus_snapshot_path == snapshot_path


@pytest.mark.anyio
async def test_run_once_opens_the_configured_snapshot_and_local_postgresql_store(
    acceptance_database_url: str, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-corpus.json"
    snapshot_path.write_text(json.dumps({"snapshots": []}))
    settings = WorkerSettings(
        database_url=acceptance_database_url,
        corpus_snapshot_path=snapshot_path,
    )

    # When
    finalizations = await run_once(settings)

    # Then
    assert finalizations == ()


@pytest.mark.anyio
async def test_run_once_routes_through_and_closes_the_shared_corpus_source(
    monkeypatch,
) -> None:
    # Given
    source = RecordingCorpusSource(legacy_snapshot_reader=object())
    worker = RecordingLeasedWorker(limits=[])
    captured: dict[str, object] = {}

    def build_source(_: WorkerSettings) -> RecordingCorpusSource:
        return source

    monkeypatch.setattr(
        "jobtology_be.workers.main.build_configured_corpus_source",
        build_source,
    )

    def build_worker(**kwargs: object) -> RecordingLeasedWorker:
        captured.update(kwargs)
        return worker

    monkeypatch.setattr(
        "jobtology_be.workers.main.build_leased_recompute_worker",
        build_worker,
    )
    settings = WorkerSettings(
        database_url="postgresql+asyncpg://worker@127.0.0.1:5432/jobtology",
        corpus_source="neo4j_query_api",
        db_link=SecretStr("graph.example.test:7687"),
        db_password=SecretStr("synthetic-query-api-password"),
        db_protocol="bolt+s://",
        worker_batch_limit=2,
    )

    # When
    finalizations = await run_once(settings)

    # Then
    assert finalizations == ()
    assert captured["snapshot_reader"] is source.legacy_snapshot_reader
    assert captured["source_snapshot_reader"] is source
    assert captured["eligible_corpus_sources"] == frozenset({"neo4j_query_api"})
    assert worker.limits == [2]
    assert source.closed


def test_worker_module_runs_once_with_explicit_local_configuration(
    acceptance_database_url: str, tmp_path: Path
) -> None:
    # Given
    snapshot_path = tmp_path / "published-corpus.json"
    snapshot_path.write_text(json.dumps({"snapshots": []}))
    environment = environ | {
        "JOBTOLOGY_DATABASE_URL": acceptance_database_url,
        "JOBTOLOGY_CORPUS_SNAPSHOT_PATH": str(snapshot_path),
    }

    # When
    result = run(
        (executable, "-m", "jobtology_be.workers.main"),
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    # Then
    assert result.returncode == 0, result.stderr
