import hashlib
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import transaction
from app.core.errors import ApiError
from app.core.jobs import Job, JobQueue
from app.core.security import hash_password, hash_token, random_password, random_token, sanitize_svg
from app.core.time import as_utc, utc_now
from app.models import (
    Gender,
    LinkSource,
    LinkStatus,
    OneTimeToken,
    OneTimeTokenKind,
    PlayerProfile,
    Profile,
    RefreshToken,
    ShareLink,
    ShareLinkKind,
    TrainerPlayerLink,
    TrainerProfile,
    User,
    UserErasure,
    UserRole,
    UserStatus,
)
from app.repositories.identity_repository import IdentityRepository
from app.schemas import (
    AvatarConfirmRequest,
    AvatarPrepareRequest,
    BrandingPatch,
    ChildLoginCreate,
    ChildLoginView,
    DependentCreate,
    DependentPatch,
    LinkView,
    PlayerView,
    ProfilePatch,
    ShareLinkCreate,
    ShareLinkView,
    TrainerProvisionRequest,
    TrainerProvisionView,
    UserView,
)
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


class IdentityService:
    def __init__(self, session: AsyncSession, settings: Settings, jobs: JobQueue) -> None:
        self.session = session
        self.settings = settings
        self.jobs = jobs
        self.repo = IdentityRepository(session)

    async def provision_trainer(self, payload: TrainerProvisionRequest, actor_id: str) -> TrainerProvisionView:
        async with transaction(self.session):
            if await self.repo.user_by_email(str(payload.email)):
                raise ApiError("Email is already in use", code="EMAIL_TAKEN", status_code=409)
            if await self.session.scalar(select(TrainerProfile).where(TrainerProfile.slug == payload.slug)):
                raise ApiError("Slug is already in use", code="SLUG_TAKEN", status_code=409)
            now = utc_now()
            user = User(
                email=str(payload.email).lower(),
                password_hash=hash_password(random_password()),
                role=UserRole.TRAINER,
                status=UserStatus.PENDING,
                must_change_password=True,
                created_at=now,
                updated_at=now,
            )
            self.session.add(user)
            await self.session.flush()
            self.session.add(
                Profile(
                    user_id=user.id,
                    first_name=payload.first_name,
                    last_name=payload.last_name,
                    phone=payload.phone,
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                )
            )
            trainer = TrainerProfile(
                user_id=user.id,
                business_name=payload.business_name,
                slug=payload.slug,
                created_at=now,
                updated_at=now,
            )
            self.session.add(trainer)
            await self.session.flush()
            setup = await AuthService(self.session, self.settings, self.jobs).issue_one_time(
                user, kind=OneTimeTokenKind.SETUP, hours=24
            )
            await self.jobs.enqueue(
                Job(
                    "send_trainer_invite",
                    {"user_id": user.id, "setup_token": setup},
                    f"setup:{user.id}",
                )
            )
            await AuditService(self.session).record("trainer.created", actor_user_id=actor_id, target_user_id=user.id)
            return TrainerProvisionView(
                id=user.id,
                email=user.email,
                business_name=trainer.business_name,
                slug=trainer.slug,
                status=UserStatus(user.status),
            )

    async def user_view(self, user: User) -> UserView:
        return await AuthService(self.session, self.settings, self.jobs).user_view(user)

    async def list_users(self, role: str | None, status: str | None, query: str | None, limit: int) -> list[UserView]:
        users = await self.repo.list_users(role=role, status=status, query_text=query, limit=limit)
        return [await self.user_view(user) for user in users]

    async def patch_user(self, user_id: str, patch: ProfilePatch, actor_id: str) -> UserView:
        async with transaction(self.session):
            user = await self.repo.user_by_id(user_id)
            profile = await self.repo.profile_by_user(user_id)
            if user is None or profile is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            for field, value in patch.model_dump(exclude_unset=True).items():
                setattr(profile, field, value)
            await AuditService(self.session).record("user.updated", actor_user_id=actor_id, target_user_id=user_id)
        return await self.user_view(user)

    async def deactivate(self, user_id: str, actor_id: str, reactivate: bool = False) -> UserView:
        async with transaction(self.session):
            user = await self.repo.user_by_id(user_id)
            if user is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            if reactivate and user.status == UserStatus.DELETED:
                raise ApiError(
                    "Deleted users cannot be reactivated",
                    code="USER_ERASED",
                    status_code=409,
                )
            user.status = UserStatus.ACTIVE if reactivate else UserStatus.INACTIVE
            if not reactivate:
                await self.session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=utc_now())
                )
            await AuditService(self.session).record(
                "user.reactivated" if reactivate else "user.deactivated",
                actor_user_id=actor_id,
                target_user_id=user_id,
            )
        return await self.user_view(user)

    async def erase(self, user_id: str, actor_id: str, reason: str | None = None) -> None:
        async with transaction(self.session):
            user = await self.repo.user_by_id(user_id)
            if user is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            if user.status == UserStatus.DELETED:
                raise ApiError(
                    "User has already been erased",
                    code="ALREADY_ERASED",
                    status_code=409,
                )
            now = utc_now()
            original_email = user.email
            original_hash = hashlib.sha256(original_email.encode()).hexdigest()
            dependents = list(
                await self.session.scalars(
                    select(PlayerProfile).where(
                        PlayerProfile.owner_user_id == user_id,
                        PlayerProfile.is_dependent.is_(True),
                    )
                )
            )
            profile = await self.repo.profile_by_user(user_id)
            if profile:
                profile.first_name = "Deleted User"
                profile.last_name = ""
                profile.phone = None
                profile.school = None
                profile.avatar_key = None
            user.email = f"erased+{user.id}@invalid"
            user.password_hash = hash_password(random_password())
            user.status = UserStatus.DELETED
            user.deleted_at = now
            await self.session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await self._erase_dependents(dependents, now)
            self.session.add(
                UserErasure(
                    original_user_id=user.id,
                    original_email_hash=original_hash,
                    erased_by_user_id=actor_id,
                    reason=reason,
                    erased_at=now,
                    legal_retention_until=date.today() + timedelta(days=365 * 7),
                    created_at=now,
                    updated_at=now,
                )
            )
            await AuditService(self.session).record("user.erased", actor_user_id=actor_id, target_user_id=user_id)

    async def _erase_dependents(self, players: list[PlayerProfile], now: datetime) -> None:
        for player in players:
            player.display_name = "Deleted Player"
            if player.login_user_id:
                child = await self.repo.user_by_id(player.login_user_id)
                if child:
                    child.email = f"erased+{child.id}@invalid"
                    child.password_hash = hash_password(random_password())
                    child.status = UserStatus.DELETED
                    child.deleted_at = now

    async def create_dependent(self, owner_id: str, payload: DependentCreate) -> PlayerView:
        age = calculate_age(payload.birth_date)
        if not 1 <= age <= 18:
            raise ApiError(
                "Dependent age must be between 1 and 18",
                code="INVALID_AGE",
                status_code=422,
            )
        async with transaction(self.session):
            duplicate = await self.session.scalar(
                select(PlayerProfile).where(
                    PlayerProfile.owner_user_id == owner_id,
                    PlayerProfile.display_name == payload.display_name,
                    PlayerProfile.birth_date == payload.birth_date,
                )
            )
            if duplicate:
                raise ApiError("Player already exists", code="DUPLICATE_PLAYER", status_code=409)
            now = utc_now()
            player = PlayerProfile(
                owner_user_id=owner_id,
                display_name=payload.display_name,
                birth_date=payload.birth_date,
                gender=payload.gender,
                is_dependent=True,
                emergency_contact_name=payload.emergency_contact_name,
                emergency_contact_phone=payload.emergency_contact_phone,
                token_spend_requires_approval=True,
                created_at=now,
                updated_at=now,
            )
            self.session.add(player)
            await self.session.flush()
            for trainer_id in payload.trainer_ids:
                if await self._owner_has_trainer(owner_id, trainer_id) is False:
                    raise ApiError("Trainer link not found", code="NOT_FOUND", status_code=404)
                await self._add_link(trainer_id, player.id, LinkSource.ADMIN)
        return await self.player_view(player)

    async def patch_dependent(self, owner_id: str, player_id: str, patch: DependentPatch) -> PlayerView:
        async with transaction(self.session):
            player = await self.repo.owned_player_by_id(owner_id, player_id)
            if player is None or not player.is_dependent:
                raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
            for field, value in patch.model_dump(exclude_unset=True).items():
                setattr(player, field, value)
        return await self.player_view(player)

    async def create_child_login(self, owner_id: str, player_id: str, payload: ChildLoginCreate) -> ChildLoginView:
        async with transaction(self.session):
            player = await self.repo.owned_player_by_id(owner_id, player_id)
            if player is None or not player.is_dependent:
                raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
            if player.login_user_id is not None:
                raise ApiError("Child login already exists", code="CHILD_LOGIN_EXISTS", status_code=409)
            if await self.repo.user_by_email(str(payload.email)) is not None:
                raise ApiError("Email is already in use", code="EMAIL_TAKEN", status_code=409)
            now = utc_now()
            child = User(
                email=str(payload.email).lower(),
                password_hash=hash_password(payload.password),
                role=UserRole.PLAYER,
                status=UserStatus.ACTIVE,
                is_email_verified=False,
                created_at=now,
                updated_at=now,
            )
            self.session.add(child)
            await self.session.flush()
            self.session.add(
                Profile(
                    user_id=child.id,
                    first_name=player.display_name,
                    last_name="",
                    timezone="UTC",
                    created_at=now,
                    updated_at=now,
                )
            )
            player.login_user_id = child.id
        return ChildLoginView(user_id=child.id, email=child.email)

    async def get_dependent(self, owner_id: str, player_id: str) -> PlayerView:
        player = await self.repo.owned_player_by_id(owner_id, player_id)
        if player is None:
            raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
        return await self.player_view(player)

    async def list_dependents(self, owner_id: str) -> list[PlayerView]:
        players = await self.repo.players_owned_by(owner_id)
        return [await self.player_view(player) for player in players]

    async def add_trainer(self, owner_id: str, player_id: str, trainer_id: str) -> PlayerView:
        async with transaction(self.session):
            player = await self.repo.owned_player_by_id(owner_id, player_id)
            if player is None:
                raise ApiError("Player not found", code="NOT_FOUND", status_code=404)
            if await self.repo.trainer_by_id(trainer_id) is None:
                raise ApiError("Trainer not found", code="NOT_FOUND", status_code=404)
            await self._add_link(trainer_id, player_id, LinkSource.ADMIN)
        return await self.player_view(player)

    async def remove_trainer(self, owner_id: str, player_id: str, trainer_id: str) -> PlayerView:
        async with transaction(self.session):
            player = await self.repo.owned_player_by_id(owner_id, player_id)
            link = await self.repo.link(trainer_id, player_id)
            if player is None or link is None:
                raise ApiError(
                    "Player or trainer link not found",
                    code="NOT_FOUND",
                    status_code=404,
                )
            link.status = LinkStatus.INACTIVE
        return await self.player_view(player)

    async def _owner_has_trainer(self, owner_id: str, trainer_id: str) -> bool:
        players = await self.repo.players_owned_by(owner_id)
        for player in players:
            if await self.repo.link_for_tenant(trainer_id, player.id):
                return True
        return False

    async def _add_link(self, trainer_id: str, player_id: str, source: LinkSource) -> TrainerPlayerLink:
        link = await self.repo.link(trainer_id, player_id)
        if link:
            link.status = LinkStatus.ACTIVE
            return link
        now = utc_now()
        link = TrainerPlayerLink(
            trainer_id=trainer_id,
            player_profile_id=player_id,
            source=source,
            linked_at=now,
            status=LinkStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def player_view(self, player: PlayerProfile) -> PlayerView:
        links = await self.repo.links_for_player(player.id)
        return PlayerView(
            id=player.id,
            owner_user_id=player.owner_user_id,
            login_user_id=player.login_user_id,
            display_name=player.display_name,
            birth_date=player.birth_date,
            gender=Gender(player.gender),
            school=player.school,
            jersey_number=player.jersey_number,
            is_dependent=player.is_dependent,
            token_spend_requires_approval=player.token_spend_requires_approval,
            links=[LinkView.model_validate(link) for link in links],
        )

    async def create_share_link(self, trainer_id: str, creator_id: str, payload: ShareLinkCreate) -> ShareLinkView:
        if payload.kind == ShareLinkKind.COACH_UNIQUE and payload.target_email is None:
            raise ApiError(
                "Coach link requires target email",
                code="TARGET_EMAIL_REQUIRED",
                status_code=422,
            )
        if payload.kind == ShareLinkKind.PLAYER_STATIC and payload.target_email is not None:
            raise ApiError(
                "Player links do not accept target email",
                code="INVALID_TARGET_EMAIL",
                status_code=422,
            )
        async with transaction(self.session):
            now = utc_now()
            link = ShareLink(
                code=random_token()[:22],
                trainer_id=trainer_id,
                created_by_user_id=creator_id,
                kind=payload.kind,
                target_email=str(payload.target_email).lower() if payload.target_email else None,
                expires_at=now + timedelta(days=7) if payload.kind == ShareLinkKind.COACH_UNIQUE else None,
                max_uses=1 if payload.kind == ShareLinkKind.COACH_UNIQUE else None,
                use_count=0,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            self.session.add(link)
            await self.session.flush()
        return ShareLinkView.model_validate(link)

    async def list_share_links(self, trainer_id: str) -> list[ShareLinkView]:
        return [ShareLinkView.model_validate(link) for link in await self.repo.share_links_for_trainer(trainer_id)]

    async def deactivate_share_link(self, trainer_id: str, link_id: str) -> None:
        async with transaction(self.session):
            link = await self.session.scalar(
                select(ShareLink).where(ShareLink.id == link_id, ShareLink.trainer_id == trainer_id)
            )
            if link is None:
                raise ApiError("ShareLink not found", code="NOT_FOUND", status_code=404)
            link.is_active = False

    async def branding(self, trainer_id: str) -> TrainerProfile:
        trainer = await self.repo.trainer_by_id(trainer_id)
        if trainer is None:
            raise ApiError("Trainer not found", code="NOT_FOUND", status_code=404)
        return trainer

    async def patch_branding(self, trainer_id: str, patch: BrandingPatch) -> TrainerProfile:
        async with transaction(self.session):
            trainer = await self.repo.trainer_by_id(trainer_id)
            if trainer is None:
                raise ApiError("Trainer not found", code="NOT_FOUND", status_code=404)
            if patch.logo_key and patch.logo_key.lower().endswith(".svg"):
                # Keys are opaque; SVG content is sanitized by the upload adapter before this method.
                patch.logo_key = sanitize_svg(patch.logo_key)
            for field, value in patch.model_dump(exclude_unset=True).items():
                setattr(trainer, field, value)
        return trainer

    async def public_branding(self, slug: str) -> tuple[TrainerProfile, str]:
        trainer = await self.session.scalar(select(TrainerProfile).where(TrainerProfile.slug == slug))
        if trainer is None:
            raise ApiError("Trainer not found", code="NOT_FOUND", status_code=404)
        return trainer, trainer.logo_key or ""

    async def prepare_avatar(self, user_id: str, payload: AvatarPrepareRequest) -> dict[str, str]:
        key = f"avatars/{user_id}/{random_token()}"
        return {"upload_url": f"https://storage.invalid/{key}", "avatar_key": key}

    async def confirm_avatar(self, user_id: str, payload: AvatarConfirmRequest) -> UserView:
        async with transaction(self.session):
            profile = await self.repo.profile_by_user(user_id)
            user = await self.repo.user_by_id(user_id)
            if profile is None or user is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            profile.avatar_key = payload.avatar_key
        return await self.user_view(user)

    async def request_email_change(self, user_id: str, email: str) -> str:
        if await self.repo.user_by_email(email):
            raise ApiError("Email is already in use", code="EMAIL_TAKEN", status_code=409)
        async with transaction(self.session):
            user = await self.repo.user_by_id(user_id)
            if user is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            token = await AuthService(self.session, self.settings, self.jobs).issue_one_time(
                user, OneTimeTokenKind.EMAIL_CHANGE, payload=email.lower(), hours=24
            )
            await self.jobs.enqueue(
                Job(
                    "send_email_change",
                    {"user_id": user.id, "token": token},
                    f"email-change:{token}",
                )
            )
            return token

    async def confirm_email_change(self, user_id: str, token_value: str) -> UserView:
        async with self.session.begin():
            token = await self.session.scalar(
                select(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == hash_token(token_value),
                    OneTimeToken.kind == OneTimeTokenKind.EMAIL_CHANGE,
                )
                .with_for_update()
            )
            if (
                token is None
                or token.user_id != user_id
                or token.consumed_at
                or as_utc(token.expires_at) <= utc_now()
                or not token.payload
            ):
                raise ApiError(
                    "Email change token is invalid",
                    code="EMAIL_CHANGE_TOKEN_INVALID",
                    status_code=409,
                )
            user = await self.repo.user_by_id(user_id)
            if user is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            user.email = token.payload
            user.is_email_verified = False
            token.consumed_at = utc_now()
        return await self.user_view(user)


def calculate_age(birth_date: date, today: date | None = None) -> int:
    today = today or date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
