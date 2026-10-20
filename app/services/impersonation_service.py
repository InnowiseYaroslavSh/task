from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import transaction
from app.core.errors import ApiError
from app.core.jobs import JobQueue
from app.core.time import new_id, utc_now
from app.models import ImpersonationSession, UserRole
from app.repositories.identity_repository import IdentityRepository
from app.schemas import TokenResponse
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService


class ImpersonationService:
    def __init__(self, session: AsyncSession, settings: Settings, jobs: JobQueue) -> None:
        self.session = session
        self.settings = settings
        self.jobs = jobs
        self.repo = IdentityRepository(session)

    async def start(self, admin_id: str, target_id: str, reason: str | None = None) -> TokenResponse:
        async with transaction(self.session):
            target = await self.repo.user_by_id(target_id)
            if target is None:
                raise ApiError("User not found", code="NOT_FOUND", status_code=404)
            if target.role == UserRole.SUPER_ADMIN:
                raise ApiError(
                    "Super admins cannot be impersonated",
                    code="CANNOT_IMPERSONATE_ADMIN",
                    status_code=403,
                )
            now = utc_now()
            session = ImpersonationSession(
                admin_user_id=admin_id,
                target_user_id=target_id,
                started_at=now,
                expires_at=now + timedelta(minutes=60),
                reason=reason,
                jti=new_id(),
                created_at=now,
                updated_at=now,
            )
            self.session.add(session)
            await self.session.flush()
            trainer_id = await AuthService(self.session, self.settings, self.jobs)._principal_trainer(target)
            response = await AuthService(self.session, self.settings, self.jobs)._issue_tokens(
                target,
                trainer_id=trainer_id,
                act=admin_id,
                impersonation_session_id=session.id,
                ttl_minutes=60,
                token_jti=session.jti,
            )
            await AuditService(self.session).record(
                "impersonation.started",
                actor_user_id=admin_id,
                target_user_id=target_id,
            )
        return response

    async def exit(self, target_id: str, jti: str | None) -> None:
        if not jti:
            raise ApiError("Not impersonated", code="NOT_IMPERSONATED", status_code=409)
        async with transaction(self.session):
            session = await self.repo.impersonation_by_jti(jti)
            if session is None or session.target_user_id != target_id:
                raise ApiError("Impersonation session not found", code="NOT_FOUND", status_code=404)
            session.ended_at = utc_now()
            await AuditService(self.session).record(
                "impersonation.ended",
                actor_user_id=session.admin_user_id,
                target_user_id=target_id,
            )
