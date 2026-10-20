from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.models.base import Base


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, future=True, echo=False)


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def close_db(engine: AsyncEngine) -> None:
    await engine.dispose()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def default_settings() -> Settings:
    return get_settings()


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[None, None]:
    """Start a service transaction after closing a read-only dependency transaction."""

    if session.in_transaction():
        await session.rollback()
    async with session.begin():
        yield
