from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AdminUserPatch,
    ImpersonateRequest,
    TokenResponse,
    TrainerProvisionRequest,
    TrainerProvisionView,
    UserListView,
    UserView,
)
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, require_non_impersonated_admin
from app.core.errors import ApiError
from app.services.identity_service import IdentityService
from app.services.impersonation_service import ImpersonationService

router = APIRouter()


def identity(request: Request, session: AsyncSession) -> IdentityService:
    return IdentityService(session, request.app.state.settings, request.app.state.jobs)


@router.get("/users", response_model=UserListView, summary="List users")
async def list_users(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> UserListView:
    require_non_impersonated_admin(principal)
    items = await identity(request, session).list_users(role, status, query, limit)
    return UserListView(items=items, has_more=len(items) == limit)


@router.post(
    "/users/trainers",
    response_model=TrainerProvisionView,
    status_code=201,
    summary="Provision a trainer",
)
async def provision_trainer(
    payload: TrainerProvisionRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TrainerProvisionView:
    require_non_impersonated_admin(principal)
    return await identity(request, session).provision_trainer(payload, principal.user_id)


@router.patch("/users/{user_id}", response_model=UserView, summary="Update a user profile")
async def patch_user(
    user_id: str,
    payload: AdminUserPatch,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    require_non_impersonated_admin(principal)
    patch = payload.model_dump(exclude_unset=True)
    from app.api.v1.schemas import ProfilePatch

    return await identity(request, session).patch_user(user_id, ProfilePatch(**patch), principal.user_id)


@router.get("/users/{user_id}", response_model=UserView, summary="Get a user")
async def get_user(
    user_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    require_non_impersonated_admin(principal)
    service = identity(request, session)
    user = await service.repo.user_by_id(user_id)
    if user is None:
        raise ApiError("User not found", code="NOT_FOUND", status_code=404)
    return await service.user_view(user)


@router.post("/users/{user_id}/deactivate", response_model=UserView, summary="Deactivate a user")
async def deactivate(
    user_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    require_non_impersonated_admin(principal)
    return await identity(request, session).deactivate(user_id, principal.user_id)


@router.post("/users/{user_id}/reactivate", response_model=UserView, summary="Reactivate a user")
async def reactivate(
    user_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    require_non_impersonated_admin(principal)
    return await identity(request, session).deactivate(user_id, principal.user_id, reactivate=True)


@router.delete("/users/{user_id}", status_code=204, summary="Erase a user")
async def erase(
    user_id: str,
    principal: CurrentPrincipal,
    request: Request,
    reason: str | None = Query(default=None, max_length=1000),
    session: AsyncSession = Depends(get_db),
) -> Response:
    require_non_impersonated_admin(principal)
    await identity(request, session).erase(user_id, principal.user_id, reason)
    return Response(status_code=204)


@router.post(
    "/users/{user_id}/impersonate",
    response_model=TokenResponse,
    summary="Impersonate a user",
)
async def impersonate(
    user_id: str,
    payload: ImpersonateRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    require_non_impersonated_admin(principal)
    return await ImpersonationService(session, request.app.state.settings, request.app.state.jobs).start(
        principal.user_id, user_id, payload.reason
    )
