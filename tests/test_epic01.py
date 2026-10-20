from datetime import time

import pytest
from app.api.v1.schemas import AvailabilityInput, AvailabilityReplace
from app.core.errors import ApiError
from app.core.security import hash_password
from app.core.time import utc_now
from app.models import (
    AuditEvent,
    PlayerProfile,
    Profile,
    RefreshToken,
    ShareLink,
    ShareLinkKind,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserRole,
    UserStatus,
)
from app.services.availability_service import AvailabilityService
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import login


@pytest.mark.asyncio
async def test_super_admin_can_provision_trainer_without_exposing_password(client: AsyncClient) -> None:
    tokens = await login(client)
    response = await client.post(
        "/api/v1/admin/users/trainers",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={
            "email": "trainer@example.com",
            "first_name": "Bob",
            "last_name": "Ray",
            "business_name": "Hoops Academy",
            "slug": "hoops-academy",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert "password" not in body
    duplicate = await client.post(
        "/api/v1/admin/users/trainers",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        json={
            "email": "trainer@example.com",
            "first_name": "Bob",
            "last_name": "Ray",
            "business_name": "Other",
            "slug": "other",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.asyncio
async def test_invited_trainer_setup_and_deactivation_revokes_refresh_token(
    application: FastAPI, client: AsyncClient
) -> None:
    admin_tokens = await login(client)
    provisioned = await client.post(
        "/api/v1/admin/users/trainers",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={
            "email": "setup@example.com",
            "first_name": "Setup",
            "last_name": "Trainer",
            "business_name": "Setup Academy",
            "slug": "setup-academy",
        },
    )
    assert provisioned.status_code == 201
    trainer_id = provisioned.json()["id"]
    setup_job = next(job for job in application.state.jobs.jobs if job.name == "send_trainer_invite")
    completed = await client.post(
        "/api/v1/auth/setup",
        json={"token": setup_job.payload["setup_token"], "password": "StrongPassword2"},
    )
    assert completed.status_code == 200
    assert completed.json()["user"]["status"] == "active"
    trainer_refresh = completed.json()["refresh_token"]

    deactivated = await client.post(
        f"/api/v1/admin/users/{trainer_id}/deactivate",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "inactive"
    revoked = await client.post("/api/v1/auth/refresh", json={"refresh_token": trainer_refresh})
    assert revoked.status_code == 401

    async with application.state.session_factory() as session:
        token = await session.scalar(select(RefreshToken).where(RefreshToken.user_id == trainer_id))
        assert token is not None
        assert token.revoked_at is not None


@pytest.mark.asyncio
async def test_share_link_registration_returns_tokens_and_rejects_existing_anonymous_account(
    application: FastAPI, client: AsyncClient
) -> None:
    async with application.state.session_factory() as session:
        now = utc_now()
        trainer = User(
            email="trainer@example.com",
            password_hash=hash_password("StrongPassword1"),
            role=UserRole.TRAINER,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        session.add(trainer)
        await session.flush()
        session.add(
            Profile(
                user_id=trainer.id,
                first_name="Trainer",
                last_name="One",
                timezone="UTC",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TrainerProfile(user_id=trainer.id, business_name="Hoops", slug="hoops", created_at=now, updated_at=now)
        )
        link = ShareLink(
            code="PLAYER-CODE-123456789",
            trainer_id=trainer.id,
            created_by_user_id=trainer.id,
            kind=ShareLinkKind.PLAYER_STATIC,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(link)
        await session.commit()

    registered = await client.post(
        "/api/v1/auth/register?share_code=PLAYER-CODE-123456789",
        json={
            "email": "player@example.com",
            "password": "StrongPassword1",
            "first_name": "Pat",
            "last_name": "Player",
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["user"]["role"] == "player"

    existing = await client.post(
        "/api/v1/auth/register?share_code=PLAYER-CODE-123456789",
        json={
            "email": "player@example.com",
            "password": "StrongPassword1",
            "first_name": "Pat",
            "last_name": "Player",
        },
    )
    assert existing.status_code == 409
    assert existing.json()["error"]["details"]["action"] == "login_then_redeem"


@pytest.mark.asyncio
async def test_child_cannot_redeem_share_link_and_parent_is_notified(application: FastAPI, client: AsyncClient) -> None:
    async with application.state.session_factory() as session:
        now = utc_now()
        parent = User(
            email="parent@example.com",
            password_hash=hash_password("StrongPassword1"),
            role=UserRole.PLAYER,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        trainer = User(
            email="child-trainer@example.com",
            password_hash=hash_password("StrongPassword1"),
            role=UserRole.TRAINER,
            status=UserStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        session.add_all([parent, trainer])
        await session.flush()
        session.add_all(
            [
                Profile(
                    user_id=parent.id,
                    first_name="Parent",
                    last_name="One",
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                ),
                Profile(
                    user_id=trainer.id,
                    first_name="Coach",
                    last_name="One",
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                ),
                TrainerProfile(
                    user_id=trainer.id,
                    business_name="Child Academy",
                    slug="child-academy",
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.flush()
        player = PlayerProfile(
            owner_user_id=parent.id,
            display_name="Child One",
            birth_date=now.date().replace(year=now.year - 10),
            gender="unspecified",
            is_dependent=True,
            created_at=now,
            updated_at=now,
        )
        session.add(player)
        await session.flush()
        session.add(
            ShareLink(
                code="CHILD-CODE-123456789",
                trainer_id=trainer.id,
                created_by_user_id=trainer.id,
                kind=ShareLinkKind.PLAYER_STATIC,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            TrainerPlayerLink(
                trainer_id=trainer.id,
                player_profile_id=player.id,
                source="admin",
                linked_at=now,
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    parent_tokens = await login(client, "parent@example.com")
    child_login = await client.post(
        f"/api/v1/me/players/{player.id}/login",
        headers={"Authorization": f"Bearer {parent_tokens['access_token']}"},
        json={"email": "child@example.com", "password": "StrongPassword1"},
    )
    assert child_login.status_code == 201
    child_tokens = await login(client, "child@example.com")
    blocked = await client.post(
        "/api/v1/auth/register?share_code=CHILD-CODE-123456789",
        headers={"Authorization": f"Bearer {child_tokens['access_token']}"},
        json={
            "email": "blocked@example.com",
            "password": "StrongPassword1",
            "first_name": "Blocked",
            "last_name": "Child",
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CHILD_CANNOT_REDEEM"
    assert any(job.name == "notify_parent_share_link_blocked" for job in application.state.jobs.jobs)


@pytest.mark.asyncio
async def test_login_rate_limit_returns_retry_after(application: FastAPI, client: AsyncClient) -> None:
    application.state.rate_limiter.limit = 1
    first = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "StrongPassword1"},
    )
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "StrongPassword1"},
    )
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"


@pytest.mark.asyncio
async def test_refresh_reuse_revokes_family(client: AsyncClient) -> None:
    tokens = await login(client)
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "REFRESH_REUSE_DETECTED"
    family_token = await client.post("/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]})
    assert family_token.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_blocks_admin_routes_audits_request_and_exits(
    application: FastAPI, client: AsyncClient
) -> None:
    admin_tokens = await login(client)
    provisioned = await client.post(
        "/api/v1/admin/users/trainers",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={
            "email": "impersonated@example.com",
            "first_name": "Impersonated",
            "last_name": "Trainer",
            "business_name": "Impersonation Academy",
            "slug": "impersonation-academy",
        },
    )
    trainer_id = provisioned.json()["id"]
    setup_job = next(job for job in application.state.jobs.jobs if job.name == "send_trainer_invite")
    setup = await client.post(
        "/api/v1/auth/setup",
        json={"token": setup_job.payload["setup_token"], "password": "StrongPassword2"},
    )
    assert setup.status_code == 200

    impersonated = await client.post(
        f"/api/v1/admin/users/{trainer_id}/impersonate",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={"reason": "support"},
    )
    assert impersonated.status_code == 200
    impersonated_headers = {"Authorization": f"Bearer {impersonated.json()['access_token']}"}
    me = await client.get("/api/v1/me", headers=impersonated_headers)
    assert me.status_code == 200
    assert me.headers["x-impersonated-by"]
    admin_blocked = await client.get("/api/v1/admin/users", headers=impersonated_headers)
    assert admin_blocked.status_code == 403

    exited = await client.post("/api/v1/auth/impersonation/exit", headers=impersonated_headers)
    assert exited.status_code == 204
    expired = await client.get("/api/v1/me", headers=impersonated_headers)
    assert expired.status_code == 401
    async with application.state.session_factory() as session:
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "impersonation.request",
                AuditEvent.target_user_id == trainer_id,
            )
        )
        assert event is not None


@pytest.mark.asyncio
async def test_gdpr_erasure_anonymizes_user_and_is_irreversible(application: FastAPI, client: AsyncClient) -> None:
    admin_tokens = await login(client)
    provisioned = await client.post(
        "/api/v1/admin/users/trainers",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={
            "email": "erase@example.com",
            "first_name": "Erase",
            "last_name": "Me",
            "business_name": "Erase Academy",
            "slug": "erase-academy",
        },
    )
    trainer_id = provisioned.json()["id"]
    erased = await client.delete(
        f"/api/v1/admin/users/{trainer_id}",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
    )
    assert erased.status_code == 204
    again = await client.delete(
        f"/api/v1/admin/users/{trainer_id}",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
    )
    assert again.status_code == 409
    async with application.state.session_factory() as session:
        user = await session.get(User, trainer_id)
        profile = await session.get(Profile, trainer_id)
        assert user is not None and user.status == UserStatus.DELETED
        assert user.email == f"erased+{trainer_id}@invalid"
        assert profile is not None and profile.first_name == "Deleted User"
        assert profile.last_name == ""


@pytest.mark.asyncio
async def test_availability_rejects_overlap_and_honors_boundary() -> None:
    payload = AvailabilityReplace(
        windows=[
            AvailabilityInput(weekday=1, start_time=time(16), end_time=time(18)),
        ]
    )
    assert AvailabilityService._validate(payload) is None
    with pytest.raises(ApiError, match="overlap"):
        AvailabilityService._validate(
            AvailabilityReplace(
                windows=[
                    AvailabilityInput(weekday=1, start_time=time(16), end_time=time(18)),
                    AvailabilityInput(weekday=1, start_time=time(17), end_time=time(19)),
                ]
            )
        )


@pytest.mark.asyncio
async def test_cross_tenant_player_lookup_is_not_exposed(client: AsyncClient) -> None:
    tokens = await login(client)
    response = await client.get(
        "/api/v1/me/players/not-a-real-player",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 403
