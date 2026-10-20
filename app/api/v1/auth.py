from typing import cast

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RegisterRequest,
    SetupRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.core.db import get_db
from app.core.deps import CurrentPrincipal, OptionalPrincipal
from app.core.errors import ApiError
from app.core.jobs import Job, JobQueue
from app.core.rate_limit import InMemoryRateLimiter
from app.models import OneTimeTokenKind
from app.services.auth_service import AuthService

router = APIRouter()


def jobs_for(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.jobs)


def limiter_for(request: Request) -> InMemoryRateLimiter:
    return cast(InMemoryRateLimiter, request.app.state.rate_limiter)


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    summary="Register through a ShareLink",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    principal: OptionalPrincipal,
    share_code: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    service = AuthService(session, request.app.state.settings, jobs_for(request))
    status, result = await service.register(payload, share_code, principal.user_id if principal else None)
    response.status_code = status
    return result


@router.post("/login", response_model=TokenResponse, summary="Log in")
async def login(payload: LoginRequest, request: Request, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    client_host = request.client.host if request.client else "unknown"
    limiter_for(request).check(f"login:{client_host}")
    limiter_for(request).check(f"login-email:{str(payload.email).lower()}")
    return await AuthService(session, request.app.state.settings, jobs_for(request)).login(
        str(payload.email), payload.password
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rotate a refresh token")
async def refresh(payload: RefreshRequest, request: Request, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    return await AuthService(session, request.app.state.settings, jobs_for(request)).refresh(payload.refresh_token)


@router.post("/setup", response_model=TokenResponse, summary="Complete an invited account setup")
async def setup(payload: SetupRequest, request: Request, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    return await AuthService(session, request.app.state.settings, jobs_for(request)).complete_setup(
        payload.token, payload.password
    )


@router.post("/logout", status_code=204, summary="Revoke a refresh-token family")
async def logout(payload: RefreshRequest, request: Request, session: AsyncSession = Depends(get_db)) -> Response:
    await AuthService(session, request.app.state.settings, jobs_for(request)).logout(payload.refresh_token)
    return Response(status_code=204)


@router.post("/password-reset/request", status_code=202, summary="Request a password reset")
async def password_reset_request(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    await AuthService(session, request.app.state.settings, jobs_for(request)).request_password_reset(str(payload.email))
    return Response(status_code=202)


@router.post("/password-reset/confirm", status_code=204, summary="Confirm a password reset")
async def password_reset_confirm(
    payload: PasswordResetConfirm,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    await AuthService(session, request.app.state.settings, jobs_for(request)).confirm_password_reset(
        payload.token, payload.password
    )
    return Response(status_code=204)


@router.post("/verify-email", status_code=204, summary="Verify an email")
async def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    await AuthService(session, request.app.state.settings, jobs_for(request)).verify_email(payload.token)
    return Response(status_code=204)


@router.post(
    "/verify-email/resend",
    status_code=202,
    summary="Resend an email verification token",
)
async def resend_verification(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    service = AuthService(session, request.app.state.settings, jobs_for(request))
    user = await service.repo.user_by_id(principal.user_id)
    if user is None:
        raise ApiError("User not found", code="NOT_FOUND", status_code=404)
    await session.rollback()
    from app.core.db import transaction

    async with transaction(session):
        token = await service.issue_one_time(user, kind=OneTimeTokenKind.EMAIL_VERIFICATION, hours=24)
        await service.jobs.enqueue(
            Job(
                "send_verification_email",
                {"user_id": user.id, "token": token},
                f"verification:{token}",
            )
        )
    return Response(status_code=202)


@router.post("/impersonation/exit", status_code=204, summary="Exit impersonation")
async def impersonation_exit(
    principal: CurrentPrincipal,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    from app.services.impersonation_service import ImpersonationService

    await ImpersonationService(
        session,
        request.app.state.settings,
        request.app.state.jobs,
    ).exit(principal.user_id, principal.token_jti)
    return Response(status_code=204)
