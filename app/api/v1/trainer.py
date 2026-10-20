import secrets

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AvailabilityView,
    BrandingPatch,
    BrandingView,
    LogoPrepareRequest,
    LogoPrepareView,
    ShareLinkCreate,
    ShareLinkView,
)
from app.core.db import get_db
from app.core.deps import CurrentPrincipal
from app.core.errors import ApiError, PermissionDeniedError
from app.models import SubjectType, UserRole
from app.services.availability_service import AvailabilityService
from app.services.identity_service import IdentityService

router = APIRouter()


def require_trainer(principal: CurrentPrincipal) -> str:
    if principal.role not in (UserRole.TRAINER, UserRole.SUPER_ADMIN) or principal.acting_admin_id:
        raise PermissionDeniedError("Trainer access required")
    return principal.trainer_id or principal.user_id


def service(request: Request, session: AsyncSession) -> IdentityService:
    return IdentityService(session, request.app.state.settings, request.app.state.jobs)


@router.get(
    "/share-links",
    response_model=list[ShareLinkView],
    summary="List trainer ShareLinks",
)
async def list_share_links(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> list[ShareLinkView]:
    trainer_id = require_trainer(principal)
    return await service(request, session).list_share_links(trainer_id)


@router.post(
    "/share-links",
    response_model=ShareLinkView,
    status_code=201,
    summary="Issue a trainer ShareLink",
)
async def create_share_link(
    payload: ShareLinkCreate,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ShareLinkView:
    trainer_id = require_trainer(principal)
    return await service(request, session).create_share_link(trainer_id, principal.user_id, payload)


@router.delete("/share-links/{link_id}", status_code=204, summary="Deactivate a ShareLink")
async def delete_share_link(
    link_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    trainer_id = require_trainer(principal)
    await service(request, session).deactivate_share_link(trainer_id, link_id)
    return Response(status_code=204)


@router.get(
    "/players/{player_id}/availability",
    response_model=list[AvailabilityView],
    summary="Read player availability",
)
async def player_availability(
    player_id: str, principal: CurrentPrincipal, session: AsyncSession = Depends(get_db)
) -> list[AvailabilityView]:
    trainer_id = require_trainer(principal)
    from app.repositories.identity_repository import IdentityRepository

    if await IdentityRepository(session).link_for_tenant(trainer_id, player_id) is None:
        raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
    return await AvailabilityService(session).get(SubjectType.PLAYER, player_id)


@router.get("/branding", response_model=BrandingView, summary="Get trainer branding")
async def get_branding(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> BrandingView:
    trainer = await service(request, session).branding(require_trainer(principal))
    return BrandingView(
        business_name=trainer.business_name,
        brand_primary_color=trainer.brand_primary_color,
        brand_secondary_color=trainer.brand_secondary_color,
        logo_url=f"https://storage.invalid/{trainer.logo_key}" if trainer.logo_key else None,
    )


@router.patch("/branding", response_model=BrandingView, summary="Update trainer branding")
async def patch_branding(
    payload: BrandingPatch,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> BrandingView:
    trainer = await service(request, session).patch_branding(require_trainer(principal), payload)
    return BrandingView(
        business_name=trainer.business_name,
        brand_primary_color=trainer.brand_primary_color,
        brand_secondary_color=trainer.brand_secondary_color,
        logo_url=f"https://storage.invalid/{trainer.logo_key}" if trainer.logo_key else None,
    )


@router.put(
    "/branding/logo",
    response_model=LogoPrepareView,
    summary="Prepare a branding logo upload",
)
async def prepare_logo(payload: LogoPrepareRequest, principal: CurrentPrincipal, request: Request) -> LogoPrepareView:
    trainer_id = require_trainer(principal)
    key = f"logos/{trainer_id}/{secrets.token_urlsafe(24)}"
    return LogoPrepareView(upload_url=f"https://storage.invalid/{key}", logo_key=key)
