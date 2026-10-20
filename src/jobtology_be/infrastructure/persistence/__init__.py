"""PostgreSQL-backed application persistence."""

__all__ = ["Database", "PostgresApplicationStore"]


def __getattr__(name: str):
    # Importing a persistence contract must not eagerly construct the application
    # store (which itself imports application models, including structured specs).
    if name == "Database":
        from jobtology_be.infrastructure.persistence.database import Database
        return Database
    if name == "PostgresApplicationStore":
        from jobtology_be.infrastructure.persistence.store import PostgresApplicationStore
        return PostgresApplicationStore
    raise AttributeError(name)
