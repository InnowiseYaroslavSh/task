from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import ApiError, AuthenticationError, PermissionDeniedError
from app.core.security import decode_access_token
from app.core.time import as_utc, utc_now
from app.models import ImpersonationSession, User, UserRole, UserStatus

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: UserRole
    trainer_id: str | None
    acting_admin_id: str | None = None
    token_jti: str | None = None
    is_child: bool = False


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Principal:
    if credentials is None:
        raise AuthenticationError("Authentication required")
    payload = decode_access_token(credentials.credentials, request.app.state.settings)
    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise AuthenticationError("Invalid access token")
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or user.status != UserStatus.ACTIVE:
        raise AuthenticationError("User is inactive")

    acting_admin_id = payload.get("act")
    if acting_admin_id is not None:
        jti = payload.get("jti")
        impersonation = await session.scalar(select(ImpersonationSession).where(ImpersonationSession.jti == jti))
        if impersonation is None or impersonation.ended_at is not None or as_utc(impersonation.expires_at) <= utc_now():
            raise ApiError(
                "Impersonation session expired",
                code="IMPERSONATION_EXPIRED",
                status_code=401,
            )
        request.state.impersonator_id = acting_admin_id

    # A child flag is derived by finding a dependent profile whose login user is this user.
    from app.models import PlayerProfile

    user_id_value = user.id
    user_role = UserRole(user.role)
    is_child = (
        await session.scalar(select(PlayerProfile.id).where(PlayerProfile.login_user_id == user_id_value)) is not None
    )
    trainer_id = payload.get("trainer_id")
    token_jti = payload.get("jti")
    await session.rollback()
    principal = Principal(
        user_id=user_id_value,
        role=user_role,
        trainer_id=trainer_id,
        acting_admin_id=acting_admin_id,
        token_jti=token_jti,
        is_child=is_child,
    )
    request.state.principal = principal
    if acting_admin_id is not None:
        request.state.impersonated_target_id = user_id_value
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


async def get_optional_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Principal | None:
    if credentials is None:
        return None
    return await get_current_principal(request, credentials, session)


OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]


def require_role(*roles: UserRole) -> Callable[[Principal], Awaitable[Principal]]:
    async def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            raise PermissionDeniedError("This role cannot perform the requested action")
        return principal

    return dependency


def require_tenant_scope(principal: CurrentPrincipal, trainer_id: str) -> Principal:
    if principal.role == UserRole.SUPER_ADMIN and principal.acting_admin_id is None:
        return principal
    if principal.trainer_id != trainer_id:
        raise ApiError("Tenant resource not found", code="NOT_FOUND", status_code=404)
    return principal


def require_non_impersonated_admin(principal: CurrentPrincipal) -> Principal:
    if principal.role != UserRole.SUPER_ADMIN or principal.acting_admin_id is not None:
        raise PermissionDeniedError("Only a non-impersonated super admin can perform this action")
    return principal
