from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    ApprovalDecision,
    ApprovalView,
    AvailabilityReplace,
    AvailabilityView,
    AvatarConfirmRequest,
    AvatarPrepareRequest,
    ChildLoginCreate,
    ChildLoginView,
    ContextView,
    DependentCreate,
    DependentPatch,
    EmailChangeConfirm,
    EmailChangeRequest,
    PlayerView,
    ProfilePatch,
    TrainerAssociationRequest,
    UserView,
)
from app.core.db import get_db, transaction
from app.core.deps import CurrentPrincipal
from app.core.errors import ApiError, PermissionDeniedError
from app.models import LinkStatus, PlayerProfile, SubjectType, TrainerPlayerLink, UserRole
from app.services.approval_service import ApprovalService
from app.services.availability_service import AvailabilityService
from app.services.identity_service import IdentityService

router = APIRouter()


def service(request: Request, session: AsyncSession) -> IdentityService:
    return IdentityService(session, request.app.state.settings, request.app.state.jobs)


def require_player(principal: CurrentPrincipal) -> None:
    if principal.role != UserRole.PLAYER:
        raise PermissionDeniedError("Only players can perform this action")
    if principal.is_child:
        raise PermissionDeniedError("Child accounts cannot perform this action")


@router.get("", response_model=UserView, summary="Get the current profile")
async def get_me(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    user = await service(request, session).repo.user_by_id(principal.user_id)
    if user is None:
        raise ApiError("User not found", code="NOT_FOUND", status_code=404)
    return await service(request, session).user_view(user)


@router.patch("", response_model=UserView, summary="Update the current profile")
async def patch_me(
    payload: ProfilePatch,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    return await service(request, session).patch_user(principal.user_id, payload, principal.user_id)


@router.put("/avatar", summary="Prepare an avatar upload")
async def prepare_avatar(
    payload: AvatarPrepareRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    return await service(request, session).prepare_avatar(principal.user_id, payload)


@router.post("/avatar/confirm", response_model=UserView, summary="Confirm an avatar upload")
async def confirm_avatar(
    payload: AvatarConfirmRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    return await service(request, session).confirm_avatar(principal.user_id, payload)


@router.post("/email-change", status_code=202, summary="Request an email change")
async def email_change(
    payload: EmailChangeRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    await service(request, session).request_email_change(principal.user_id, str(payload.email))
    return Response(status_code=202)


@router.post("/email-change/confirm", response_model=UserView, summary="Confirm an email change")
async def email_change_confirm(
    payload: EmailChangeConfirm,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserView:
    return await service(request, session).confirm_email_change(principal.user_id, payload.token)


@router.get(
    "/contexts",
    response_model=list[ContextView],
    summary="List player trainer contexts",
)
async def contexts(principal: CurrentPrincipal, session: AsyncSession = Depends(get_db)) -> list[ContextView]:
    if principal.role != UserRole.PLAYER:
        raise PermissionDeniedError("Only players have trainer contexts")
    from app.repositories.identity_repository import IdentityRepository

    repo = IdentityRepository(session)
    players = await repo.players_owned_by(principal.user_id)
    contexts: list[ContextView] = []
    for player in players:
        for link in await repo.links_for_player(player.id, active_only=True):
            trainer = await repo.trainer_by_id(link.trainer_id)
            if trainer:
                contexts.append(
                    ContextView(
                        trainer_id=trainer.user_id,
                        business_name=trainer.business_name,
                        player_profile_id=player.id,
                    )
                )
    return contexts


@router.get(
    "/availability",
    response_model=list[AvailabilityView],
    summary="Get own availability",
)
async def get_availability(
    principal: CurrentPrincipal, session: AsyncSession = Depends(get_db)
) -> list[AvailabilityView]:
    subject_type, subject_id = await self_subject(session, principal)
    return await AvailabilityService(session).get(subject_type, subject_id)


@router.put(
    "/availability",
    response_model=list[AvailabilityView],
    summary="Replace own availability",
)
async def put_availability(
    payload: AvailabilityReplace,
    principal: CurrentPrincipal,
    session: AsyncSession = Depends(get_db),
) -> list[AvailabilityView]:
    subject_type, subject_id = await self_subject(session, principal)
    return await AvailabilityService(session).replace(subject_type, subject_id, payload)


@router.get("/players", response_model=list[PlayerView], summary="List owned player profiles")
async def list_players(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> list[PlayerView]:
    require_player(principal)
    return await service(request, session).list_dependents(principal.user_id)


@router.post(
    "/players",
    response_model=PlayerView,
    status_code=201,
    summary="Create a dependent profile",
)
async def create_player(
    payload: DependentCreate,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PlayerView:
    require_player(principal)
    return await service(request, session).create_dependent(principal.user_id, payload)


@router.get("/players/{player_id}", response_model=PlayerView, summary="Get a dependent profile")
async def get_player(
    player_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PlayerView:
    require_player(principal)
    return await service(request, session).get_dependent(principal.user_id, player_id)


@router.patch(
    "/players/{player_id}",
    response_model=PlayerView,
    summary="Update a dependent profile",
)
async def patch_player(
    player_id: str,
    payload: DependentPatch,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PlayerView:
    require_player(principal)
    return await service(request, session).patch_dependent(principal.user_id, player_id, payload)


@router.post(
    "/players/{player_id}/login",
    response_model=ChildLoginView,
    status_code=201,
    summary="Create a child login",
)
async def create_child_login(
    player_id: str,
    payload: ChildLoginCreate,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ChildLoginView:
    require_player(principal)
    return await service(request, session).create_child_login(principal.user_id, player_id, payload)


@router.delete("/players/{player_id}", status_code=204, summary="Deactivate a dependent profile")
async def delete_player(
    player_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    require_player(principal)
    player = await service(request, session).get_dependent(principal.user_id, player_id)
    async with transaction(session):
        for link in await session.scalars(
            select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == player.id)
        ):
            link.status = LinkStatus.INACTIVE
    return Response(status_code=204)


@router.post(
    "/players/{player_id}/trainers",
    response_model=PlayerView,
    status_code=201,
    summary="Link a player to a trainer",
)
async def add_trainer(
    player_id: str,
    payload: TrainerAssociationRequest,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PlayerView:
    require_player(principal)
    return await service(request, session).add_trainer(principal.user_id, player_id, payload.trainer_id)


@router.delete(
    "/players/{player_id}/trainers/{trainer_id}",
    response_model=PlayerView,
    summary="Unlink a player from a trainer",
)
async def remove_trainer(
    player_id: str,
    trainer_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> PlayerView:
    require_player(principal)
    return await service(request, session).remove_trainer(principal.user_id, player_id, trainer_id)


@router.get(
    "/players/{player_id}/availability",
    response_model=list[AvailabilityView],
    summary="Get dependent availability",
)
async def get_dependent_availability(
    player_id: str,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> list[AvailabilityView]:
    require_player(principal)
    player = await service(request, session).repo.owned_player_by_id(principal.user_id, player_id)
    if player is None:
        raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
    return await AvailabilityService(session).get(SubjectType.PLAYER, player.id)


@router.put(
    "/players/{player_id}/availability",
    response_model=list[AvailabilityView],
    summary="Replace dependent availability",
)
async def put_dependent_availability(
    player_id: str,
    payload: AvailabilityReplace,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> list[AvailabilityView]:
    require_player(principal)
    player = await service(request, session).repo.owned_player_by_id(principal.user_id, player_id)
    if player is None:
        raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
    return await AvailabilityService(session).replace(SubjectType.PLAYER, player.id, payload)


@router.get("/approvals", response_model=list[ApprovalView], summary="List purchase approvals")
async def approvals(principal: CurrentPrincipal, session: AsyncSession = Depends(get_db)) -> list[ApprovalView]:
    require_player(principal)
    return await ApprovalService(session, InMemoryJobsAdapter()).list_for_parent(principal.user_id)


@router.post(
    "/approvals/{approval_id}/approve",
    response_model=ApprovalView,
    summary="Approve a purchase",
)
async def approve(
    approval_id: str,
    payload: ApprovalDecision,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ApprovalView:
    require_player(principal)
    return await ApprovalService(session, request.app.state.jobs).decide(
        approval_id, principal.user_id, True, payload.parent_note
    )


@router.post(
    "/approvals/{approval_id}/deny",
    response_model=ApprovalView,
    summary="Deny a purchase",
)
async def deny(
    approval_id: str,
    payload: ApprovalDecision,
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ApprovalView:
    require_player(principal)
    return await ApprovalService(session, request.app.state.jobs).decide(
        approval_id, principal.user_id, False, payload.parent_note
    )


async def self_subject(session: AsyncSession, principal: CurrentPrincipal) -> tuple[SubjectType, str]:
    if principal.role == UserRole.COACH:
        from app.repositories.identity_repository import IdentityRepository

        coach = await IdentityRepository(session).coach_by_user(principal.user_id)
        if coach is None:
            raise ApiError("Coach profile not found", code="NOT_FOUND", status_code=404)
        return SubjectType.COACH, coach.user_id
    if principal.role == UserRole.PLAYER:
        player = await session.scalar(
            select(PlayerProfile).where(
                (PlayerProfile.login_user_id == principal.user_id)
                | ((PlayerProfile.owner_user_id == principal.user_id) & PlayerProfile.is_dependent.is_(False))
            )
        )
        if player is None:
            raise ApiError("Player profile not found", code="NOT_FOUND", status_code=404)
        return SubjectType.PLAYER, player.id
    raise PermissionDeniedError("This role has no availability")


class InMemoryJobsAdapter:
    async def enqueue(self, job: object) -> None:
        return None
