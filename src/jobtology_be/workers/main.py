from pathlib import Path
from typing import ClassVar, Self, assert_never

import anyio
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobtology_be.corpus.source_factory import build_configured_corpus_source
from jobtology_be.infrastructure.persistence.contracts import RecomputeFinalization
from jobtology_be.infrastructure.persistence.database import Database
from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
from jobtology_be.settings import CorpusSource, ExternalDatabaseProtocol
from jobtology_be.workers.recompute import build_leased_recompute_worker


class WorkerSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="JOBTOLOGY_",
        env_file=".env",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    database_url: str
    corpus_snapshot_path: Path | None = None
    corpus_source: CorpusSource = "local_json"
    db_link: SecretStr | None = None
    db_password: SecretStr | None = None
    db_protocol: ExternalDatabaseProtocol | None = None
    worker_batch_limit: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_source_settings(self) -> Self:
        match self.corpus_source:
            case "local_json":
                if self.corpus_snapshot_path is None:
                    raise ValueError("Local JSON corpus source requires a snapshot path")
            case "neo4j_query_api":
                if self.db_link is None or self.db_password is None:
                    raise ValueError(
                        "Neo4j Query API source requires database link and password metadata"
                    )
            case unreachable:
                assert_never(unreachable)
        return self


def _eligible_corpus_sources(settings: WorkerSettings) -> frozenset[CorpusSource] | None:
    match settings.corpus_source:
        case "local_json":
            return None
        case "neo4j_query_api":
            if settings.corpus_snapshot_path is None:
                return frozenset({"neo4j_query_api"})
            return None
        case unreachable:
            assert_never(unreachable)


async def run_once(settings: WorkerSettings) -> tuple[RecomputeFinalization, ...]:
    corpus_source = build_configured_corpus_source(settings)
    try:
        database = Database.create(settings.database_url)
    except Exception:
        await corpus_source.aclose()
        raise
    try:
        store = PostgresApplicationStore(database)
        worker = build_leased_recompute_worker(
            store=store,
            snapshot_reader=corpus_source.legacy_snapshot_reader,
            source_snapshot_reader=corpus_source,
            eligible_corpus_sources=_eligible_corpus_sources(settings),
        )
        return await worker.process_once(limit=settings.worker_batch_limit)
    finally:
        try:
            await corpus_source.aclose()
        finally:
            await database.dispose()


def main() -> None:
    anyio.run(run_once, WorkerSettings.model_validate({}))


if __name__ == "__main__":
    main()
