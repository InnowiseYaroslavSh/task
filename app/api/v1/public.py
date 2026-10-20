from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import BrandingView, ShareLinkPreview
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.time import as_utc, utc_now
from app.models import ShareLinkKind, TrainerProfile
from app.repositories.identity_repository import IdentityRepository

router = APIRouter()


@router.get(
    "/share-links/{code}",
    response_model=ShareLinkPreview,
    summary="Preview a ShareLink",
)
async def preview_share_link(code: str, session: AsyncSession = Depends(get_db)) -> ShareLinkPreview:
    repo = IdentityRepository(session)
    link = await repo.share_link_by_code(code)
    if link is None:
        raise ApiError("ShareLink is invalid", code="SHARE_LINK_INVALID", status_code=404)
    trainer = await repo.trainer_by_id(link.trainer_id)
    if trainer is None:
        raise ApiError("ShareLink is invalid", code="SHARE_LINK_INVALID", status_code=404)
    valid = (
        link.is_active
        and (link.expires_at is None or as_utc(link.expires_at) > utc_now())
        and (link.max_uses is None or link.use_count < link.max_uses)
    )
    return ShareLinkPreview(
        trainer_name=trainer.business_name,
        kind=ShareLinkKind(link.kind),
        valid=valid,
        expires_at=link.expires_at,
    )


@router.get(
    "/trainers/{slug}/branding",
    response_model=BrandingView,
    summary="Read public trainer branding",
)
async def public_branding(slug: str, session: AsyncSession = Depends(get_db)) -> BrandingView:
    trainer = await session.scalar(select(TrainerProfile).where(TrainerProfile.slug == slug))
    if trainer is None:
        raise ApiError("Trainer not found", code="NOT_FOUND", status_code=404)
    return BrandingView(
        business_name=trainer.business_name,
        brand_primary_color=trainer.brand_primary_color,
        brand_secondary_color=trainer.brand_secondary_color,
        logo_url=f"https://storage.invalid/{trainer.logo_key}" if trainer.logo_key else None,
    )
