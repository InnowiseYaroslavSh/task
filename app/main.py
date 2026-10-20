from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import admin, auth, me, public, trainer
from app.core.config import Settings, get_settings
from app.core.db import close_db, create_engine, init_db
from app.core.errors import DomainError, domain_error_handler, validation_error_handler
from app.core.jobs import InMemoryJobQueue
from app.core.rate_limit import InMemoryRateLimiter
from app.services.audit_service import AuditService


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    engine = create_engine(config)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.auto_create_tables:
            await init_db(engine)
        yield
        await close_db(engine)

    app = FastAPI(
        title="Accelerator Core API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.session_factory = session_factory
    app.state.jobs = InMemoryJobQueue()
    app.state.rate_limiter = InMemoryRateLimiter(config.rate_limit_per_minute)
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(me.router, prefix="/api/v1/me", tags=["me"])
    app.include_router(trainer.router, prefix="/api/v1/trainer", tags=["trainer"])
    app.include_router(public.router, prefix="/api/v1/public", tags=["public"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    app.add_exception_handler(
        DomainError,
        cast(Callable[[Request, Exception], Awaitable[Response]], domain_error_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(Callable[[Request, Exception], Awaitable[Response]], validation_error_handler),
    )

    @app.middleware("http")
    async def impersonation_header(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        impersonator_id = getattr(request.state, "impersonator_id", None)
        if impersonator_id:
            response.headers["X-Impersonated-By"] = impersonator_id
            target_id = getattr(request.state, "impersonated_target_id", None)
            if target_id:
                session_factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
                async with session_factory() as audit_session:
                    async with audit_session.begin():
                        await AuditService(audit_session).record(
                            "impersonation.request",
                            actor_user_id=impersonator_id,
                            target_user_id=target_id,
                            method=request.method,
                            path=request.url.path,
                        )
        return response

    return app


app = create_app()
