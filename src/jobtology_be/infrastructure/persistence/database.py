"""Async database resources injected by the application composition root."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True, slots=True)
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    @classmethod
    def create(cls, database_url: str) -> "Database":
        engine = create_async_engine(
            database_url,
            max_overflow=5,
            pool_pre_ping=True,
            pool_size=5,
        )
        return cls(
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False),
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
