from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models import (
    AuditEvent,
    AvailabilityWindow,
    CoachProfile,
    ImpersonationSession,
    PlayerProfile,
    Profile,
    PurchaseApproval,
    RefreshToken,
    ShareLink,
    TrainerPlayerLink,
    TrainerProfile,
    User,
)


class IdentityRepository:
    """SQL-only repository; transaction ownership remains with services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def user_by_id(self, user_id: str) -> User | None:
        return await self.session.scalar(select(User).where(User.id == user_id))

    async def user_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email.lower()))

    async def profile_by_user(self, user_id: str) -> Profile | None:
        return await self.session.get(Profile, user_id)

    async def trainer_by_id(self, trainer_id: str) -> TrainerProfile | None:
        return await self.session.get(TrainerProfile, trainer_id)

    async def coach_by_user(self, user_id: str) -> CoachProfile | None:
        return await self.session.get(CoachProfile, user_id)

    async def player_by_id(self, player_id: str) -> PlayerProfile | None:
        return await self.session.get(PlayerProfile, player_id)

    async def players_owned_by(self, owner_user_id: str) -> list[PlayerProfile]:
        result = await self.session.scalars(
            select(PlayerProfile).where(PlayerProfile.owner_user_id == owner_user_id).order_by(PlayerProfile.created_at)
        )
        return list(result)

    async def owned_player_by_id(self, owner_user_id: str, player_id: str) -> PlayerProfile | None:
        return await self.session.scalar(
            select(PlayerProfile).where(
                PlayerProfile.id == player_id,
                PlayerProfile.owner_user_id == owner_user_id,
            )
        )

    async def link(self, trainer_id: str, player_id: str) -> TrainerPlayerLink | None:
        return await self.session.scalar(
            select(TrainerPlayerLink).where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.player_profile_id == player_id,
            )
        )

    async def links_for_player(self, player_id: str, *, active_only: bool = False) -> list[TrainerPlayerLink]:
        query = select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == player_id)
        if active_only:
            query = query.where(TrainerPlayerLink.status == "active")
        return list(await self.session.scalars(query.order_by(TrainerPlayerLink.linked_at)))

    async def link_for_tenant(self, trainer_id: str, player_id: str) -> TrainerPlayerLink | None:
        return await self.session.scalar(
            select(TrainerPlayerLink).where(
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.player_profile_id == player_id,
                TrainerPlayerLink.status == "active",
            )
        )

    async def share_link_by_code(self, code: str, *, lock: bool = False) -> ShareLink | None:
        query = select(ShareLink).where(ShareLink.code == code)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def share_links_for_trainer(self, trainer_id: str) -> list[ShareLink]:
        return list(
            await self.session.scalars(
                select(ShareLink).where(ShareLink.trainer_id == trainer_id).order_by(ShareLink.created_at.desc())
            )
        )

    async def refresh_by_hash(self, token_hash: str, *, lock: bool = False) -> RefreshToken | None:
        query = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def refresh_family(self, family_id: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )

    async def availability(self, subject_type: str, subject_id: str) -> list[AvailabilityWindow]:
        return list(
            await self.session.scalars(
                select(AvailabilityWindow)
                .where(
                    AvailabilityWindow.subject_type == subject_type,
                    AvailabilityWindow.subject_id == subject_id,
                )
                .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_time)
            )
        )

    async def approval_by_id(self, approval_id: str, *, lock: bool = False) -> PurchaseApproval | None:
        query = select(PurchaseApproval).where(PurchaseApproval.id == approval_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def impersonation_by_jti(self, jti: str) -> ImpersonationSession | None:
        return await self.session.scalar(select(ImpersonationSession).where(ImpersonationSession.jti == jti))

    async def list_users(
        self,
        *,
        role: str | None = None,
        status: str | None = None,
        query_text: str | None = None,
        limit: int = 50,
    ) -> list[User]:
        query = select(User).order_by(User.created_at, User.id).limit(limit)
        if role:
            query = query.where(User.role == role)
        if status:
            query = query.where(User.status == status)
        if query_text:
            query = query.where(User.email.ilike(f"%{query_text}%"))
        return list(await self.session.scalars(query))

    async def audit_events_for_actor(self, actor_user_id: str) -> list[AuditEvent]:
        return list(await self.session.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == actor_user_id)))
