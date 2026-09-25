from os import environ

import anyio
from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from jobtology_be.infrastructure.persistence.schema import METADATA

config = context.config
target_metadata = METADATA


class MigrationConfigurationError(Exception):
    pass


def _database_url() -> str:
    database_url = context.get_x_argument(as_dictionary=True).get("database_url")
    return (
        database_url
        or environ.get("JOBTOLOGY_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
        or _raise_missing_database_url()
    )


def _raise_missing_database_url() -> str:
    raise MigrationConfigurationError("Set JOBTOLOGY_DATABASE_URL or -x database_url=...")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    engine = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    anyio.run(_run_async_migrations)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
