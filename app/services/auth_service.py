from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import transaction
from app.core.errors import ApiError, AuthenticationError
from app.core.jobs import Job, JobQueue
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    random_token,
    validate_password,
    verify_password_or_dummy,
)
from app.core.time import as_utc, new_id, utc_now
from app.models import (
    CoachProfile,
    LinkSource,
    LinkStatus,
    OneTimeToken,
    OneTimeTokenKind,
    PlayerProfile,
    Profile,
    RefreshToken,
    ShareLinkEvent,
    ShareLinkEventKind,
    ShareLinkKind,
    TrainerPlayerLink,
    User,
    UserRole,
    UserStatus,
)
from app.repositories.identity_repository import IdentityRepository
from app.schemas import ProfileView, RegisterRequest, TokenResponse, UserView
from app.services.audit_service import AuditService


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings, jobs: JobQueue) -> None:
        self.session = session
        self.settings = settings
        self.jobs = jobs
        self.repo = IdentityRepository(session)

    async def user_view(self, user: User) -> UserView:
        profile = await self.repo.profile_by_user(user.id)
        return UserView(
            id=user.id,
            email=user.email,
            role=UserRole(user.role),
            status=UserStatus(user.status),
            is_email_verified=user.is_email_verified,
            must_change_password=user.must_change_password,
            profile=ProfileView.model_validate(profile) if profile is not None else None,
        )

    async def _issue_tokens(
        self,
        user: User,
        *,
        trainer_id: str | None = None,
        act: str | None = None,
        impersonation_session_id: str | None = None,
        ttl_minutes: int | None = None,
        family_id: str | None = None,
        token_jti: str | None = None,
    ) -> TokenResponse:
        access_token, _, expires_at = create_access_token(
            user_id=user.id,
            role=str(user.role),
            trainer_id=trainer_id,
            settings=self.settings,
            act=act,
            impersonation_session_id=impersonation_session_id,
            ttl_minutes=ttl_minutes,
            jti=token_jti,
        )
        refresh_token = random_token()
        self.session.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or new_id(),
                token_hash=hash_token(refresh_token),
                expires_at=utc_now() + timedelta(days=self.settings.refresh_token_days),
                user_agent_hash=None,
                ip_hash=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=max(1, int((expires_at - utc_now()).total_seconds())),
            user=await self.user_view(user),
        )

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.repo.user_by_email(email)
        valid = verify_password_or_dummy(password, user.password_hash if user else None)
        if user is None or not valid or user.status != UserStatus.ACTIVE:
            raise AuthenticationError("Invalid credentials")
        user_id = user.id
        trainer_id = await self._principal_trainer(user)
        await self.session.rollback()
        async with transaction(self.session):
            user = await self.repo.user_by_id(user_id)
            if user is None:
                raise AuthenticationError("Invalid credentials")
            user.last_login_at = utc_now()
            response = await self._issue_tokens(user, trainer_id=trainer_id)
        return response

    async def complete_setup(self, token_value: str, password: str) -> TokenResponse:
        validate_password(password)
        async with transaction(self.session):
            token = await self.session.scalar(
                select(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == hash_token(token_value),
                    OneTimeToken.kind == OneTimeTokenKind.SETUP,
                )
                .with_for_update()
            )
            if token is None or token.consumed_at is not None or as_utc(token.expires_at) <= utc_now():
                raise ApiError("Setup token is invalid", code="SETUP_TOKEN_INVALID", status_code=409)
            user = await self.repo.user_by_id(token.user_id)
            if user is None or user.status != UserStatus.PENDING:
                raise ApiError("Setup token is invalid", code="SETUP_TOKEN_INVALID", status_code=409)
            user.password_hash = hash_password(password)
            user.status = UserStatus.ACTIVE
            user.must_change_password = False
            user.is_email_verified = True
            token.consumed_at = utc_now()
            return await self._issue_tokens(user, trainer_id=await self._principal_trainer(user))

    async def _principal_trainer(self, user: User) -> str | None:
        if user.role == UserRole.TRAINER:
            return user.id
        if user.role == UserRole.COACH:
            coach = await self.repo.coach_by_user(user.id)
            return coach.trainer_id if coach else None
        return None

    async def register(
        self,
        payload: RegisterRequest,
        share_code: str | None,
        current_user_id: str | None = None,
    ) -> tuple[int, TokenResponse]:
        if not share_code:
            raise ApiError("A ShareLink is required", code="SHARE_LINK_REQUIRED", status_code=422)
        async with transaction(self.session):
            link = await self.repo.share_link_by_code(share_code, lock=True)
            now = utc_now()
            if (
                link is None
                or not link.is_active
                or (link.expires_at is not None and as_utc(link.expires_at) <= now)
                or (link.max_uses is not None and link.use_count >= link.max_uses)
            ):
                raise ApiError("ShareLink is invalid", code="SHARE_LINK_INVALID", status_code=404)
            if current_user_id is not None:
                child = await self.session.scalar(
                    select(PlayerProfile).where(PlayerProfile.login_user_id == current_user_id)
                )
                if child is not None:
                    await self.jobs.enqueue(
                        Job(
                            "notify_parent_share_link_blocked",
                            {"player_profile_id": child.id, "share_link_id": link.id},
                            f"share-link-blocked:{child.id}:{link.id}",
                        )
                    )
                    raise ApiError(
                        "Child accounts cannot redeem ShareLinks",
                        code="CHILD_CANNOT_REDEEM",
                        status_code=403,
                    )
            existing = await self.repo.user_by_email(str(payload.email))
            if link.kind == ShareLinkKind.COACH_UNIQUE:
                if payload.role != "coach":
                    raise ApiError(
                        "This link is for a coach",
                        code="WRONG_LINK_KIND",
                        status_code=409,
                    )
                if link.target_email and link.target_email.lower() != str(payload.email).lower():
                    raise ApiError(
                        "The ShareLink email does not match",
                        code="LINK_EMAIL_MISMATCH",
                        status_code=403,
                    )
            elif payload.role == "coach":
                raise ApiError(
                    "A coach must use a coach invitation",
                    code="WRONG_LINK_KIND",
                    status_code=409,
                )

            created = existing is None
            if existing is not None and current_user_id is None:
                raise ApiError(
                    "An account already exists",
                    code="ACCOUNT_EXISTS",
                    status_code=409,
                    details={"action": "login_then_redeem"},
                )
            user = existing
            if user is None:
                user = User(
                    email=str(payload.email).lower(),
                    password_hash=hash_password(payload.password),
                    role=UserRole.COACH if link.kind == ShareLinkKind.COACH_UNIQUE else UserRole.PLAYER,
                    status=UserStatus.ACTIVE,
                    is_email_verified=False,
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

            if link.kind == ShareLinkKind.COACH_UNIQUE:
                coach = await self.repo.coach_by_user(user.id)
                if coach and coach.is_active and coach.trainer_id != link.trainer_id:
                    raise ApiError(
                        "Coach is already assigned",
                        code="COACH_ALREADY_ASSIGNED",
                        status_code=409,
                        details={"trainer_id": coach.trainer_id},
                    )
                if coach is None:
                    self.session.add(
                        CoachProfile(
                            user_id=user.id,
                            trainer_id=link.trainer_id,
                            is_public=False,
                            is_active=True,
                            joined_at=now,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            else:
                target_profiles: list[PlayerProfile] = []
                if existing is not None:
                    if current_user_id != user.id:
                        raise ApiError(
                            "Authenticated account mismatch",
                            code="ACCOUNT_EXISTS",
                            status_code=409,
                        )
                    if not payload.player_profile_ids:
                        raise ApiError(
                            "Select at least one player profile",
                            code="PROFILES_REQUIRED",
                            status_code=422,
                        )
                    for player_id in payload.player_profile_ids:
                        player = await self.repo.owned_player_by_id(user.id, player_id)
                        if player is None:
                            raise ApiError(
                                "Player profile not found",
                                code="NOT_FOUND",
                                status_code=404,
                            )
                        target_profiles.append(player)
                else:
                    player = PlayerProfile(
                        owner_user_id=user.id,
                        display_name=f"{payload.first_name} {payload.last_name}".strip(),
                        birth_date=date_for_adult(),
                        gender="unspecified",
                        is_dependent=False,
                        token_spend_requires_approval=False,
                        created_at=now,
                        updated_at=now,
                    )
                    self.session.add(player)
                    await self.session.flush()
                    target_profiles.append(player)
                for player in target_profiles:
                    existing_link = await self.repo.link(link.trainer_id, player.id)
                    if existing_link is None:
                        self.session.add(
                            TrainerPlayerLink(
                                trainer_id=link.trainer_id,
                                player_profile_id=player.id,
                                source=LinkSource.SHARE_LINK,
                                share_link_id=link.id,
                                linked_at=now,
                                status=LinkStatus.ACTIVE,
                                created_at=now,
                                updated_at=now,
                            )
                        )

            link.use_count += 1
            self.session.add(
                ShareLinkEvent(
                    share_link_id=link.id,
                    kind=ShareLinkEventKind.REGISTERED,
                    occurred_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await AuditService(self.session).record(
                "share_link.registered", actor_user_id=user.id, target_user_id=user.id
            )
            verification_token = await self.issue_one_time(
                user,
                OneTimeTokenKind.EMAIL_VERIFICATION,
                hours=24,
            )
            await self.jobs.enqueue(Job("send_welcome_email", {"user_id": user.id}, f"{user.id}:welcome"))
            await self.jobs.enqueue(
                Job(
                    "send_verification_email",
                    {"user_id": user.id, "token": verification_token},
                    f"verification:{user.id}:{verification_token}",
                )
            )
            trainer_id = link.trainer_id if user.role == UserRole.PLAYER else link.trainer_id
            tokens = await self._issue_tokens(user, trainer_id=trainer_id)
        return (201 if created else 200), tokens

    async def refresh(self, refresh_value: str) -> TokenResponse:
        reuse_detected = False
        response: TokenResponse | None = None
        async with transaction(self.session):
            token = await self.repo.refresh_by_hash(hash_token(refresh_value), lock=True)
            now = utc_now()
            if token is None or as_utc(token.expires_at) <= now or token.revoked_at is not None:
                raise AuthenticationError("Refresh token is invalid")
            if token.consumed_at is not None:
                await self.repo.refresh_family(token.family_id)
                await AuditService(self.session).record("auth.refresh_reuse_detected", target_user_id=token.user_id)
                reuse_detected = True
            else:
                user = await self.repo.user_by_id(token.user_id)
                if user is None or user.status != UserStatus.ACTIVE:
                    raise AuthenticationError("Refresh token is invalid")
                token.consumed_at = now
                response = await self._issue_tokens(
                    user,
                    trainer_id=await self._principal_trainer(user),
                    family_id=token.family_id,
                )
        if reuse_detected:
            raise ApiError(
                "Refresh token reuse detected",
                code="REFRESH_REUSE_DETECTED",
                status_code=401,
            )
        assert response is not None
        return response

    async def logout(self, refresh_value: str) -> None:
        async with transaction(self.session):
            token = await self.repo.refresh_by_hash(hash_token(refresh_value), lock=True)
            if token:
                await self.repo.refresh_family(token.family_id)

    async def issue_one_time(
        self,
        user: User,
        kind: OneTimeTokenKind,
        *,
        payload: str | None = None,
        hours: int = 24,
    ) -> str:
        value = random_token()
        now = utc_now()
        self.session.add(
            OneTimeToken(
                user_id=user.id,
                kind=kind,
                token_hash=hash_token(value),
                payload=payload,
                expires_at=now + timedelta(hours=hours),
                created_at=now,
                updated_at=now,
            )
        )
        await self.session.flush()
        return value

    async def request_password_reset(self, email: str) -> None:
        async with transaction(self.session):
            user = await self.repo.user_by_email(email)
            if user:
                token = await self.issue_one_time(user, OneTimeTokenKind.PASSWORD_RESET, hours=1)
                await self.jobs.enqueue(
                    Job(
                        "send_password_reset",
                        {"user_id": user.id, "token": token},
                        f"reset:{user.id}:{token}",
                    )
                )

    async def confirm_password_reset(self, token_value: str, password: str) -> None:
        validate_password(password)
        async with transaction(self.session):
            token = await self.session.scalar(
                select(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == hash_token(token_value),
                    OneTimeToken.kind == OneTimeTokenKind.PASSWORD_RESET,
                )
                .with_for_update()
            )
            if token is None or token.consumed_at or as_utc(token.expires_at) <= utc_now():
                raise ApiError(
                    "Password reset token is invalid",
                    code="RESET_TOKEN_INVALID",
                    status_code=409,
                )
            user = await self.repo.user_by_id(token.user_id)
            if user is None:
                raise AuthenticationError("Password reset token is invalid")
            user.password_hash = hash_password(password)
            token.consumed_at = utc_now()
            await self.session.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.user_id == token.user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=utc_now())
            )

    async def verify_email(self, token_value: str) -> None:
        async with transaction(self.session):
            token = await self.session.scalar(
                select(OneTimeToken)
                .where(
                    OneTimeToken.token_hash == hash_token(token_value),
                    OneTimeToken.kind == OneTimeTokenKind.EMAIL_VERIFICATION,
                )
                .with_for_update()
            )
            if token is None or token.consumed_at or as_utc(token.expires_at) <= utc_now():
                raise ApiError(
                    "Verification token is invalid",
                    code="VERIFICATION_TOKEN_INVALID",
                    status_code=409,
                )
            user = await self.repo.user_by_id(token.user_id)
            if user is None:
                raise AuthenticationError("Verification token is invalid")
            user.is_email_verified = True
            token.consumed_at = utc_now()


def date_for_adult() -> date:
    return date(1980, 1, 1)
