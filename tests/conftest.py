from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import httpx
import pytest_asyncio
from app.core.config import Settings
from app.core.security import hash_password
from app.core.time import utc_now
from app.main import create_app
from app.models import Profile, User, UserRole, UserStatus
from fastapi import FastAPI


@pytest_asyncio.fixture
async def application(tmp_path: Path) -> AsyncIterator[FastAPI]:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        jwt_secret="test-secret-value-that-is-long-enough",
        rate_limit_per_minute=1000,
        auto_create_tables=True,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            now = utc_now()
            admin = User(
                email="admin@example.com",
                password_hash=hash_password("StrongPassword1"),
                role=UserRole.SUPER_ADMIN,
                status=UserStatus.ACTIVE,
                is_email_verified=True,
                created_at=now,
                updated_at=now,
            )
            session.add(admin)
            await session.flush()
            session.add(
                Profile(
                    user_id=admin.id,
                    first_name="Admin",
                    last_name="User",
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        yield app


@pytest_asyncio.fixture
async def client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    ) as value:
        yield value


async def login(client: httpx.AsyncClient, email: str = "admin@example.com") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPassword1"},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, str], response.json())
