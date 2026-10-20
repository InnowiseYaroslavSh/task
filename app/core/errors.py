from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})


class ApiError(DomainError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.code = code
        self.status_code = status_code


class AuthenticationError(DomainError):
    status_code = 401
    code = "INVALID_CREDENTIALS"


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "FORBIDDEN"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"


class RateLimitedError(DomainError):
    status_code = 429
    code = "RATE_LIMITED"


def error_payload(code: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": dict(details or {})}}


async def domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
    headers = {"Retry-After": "60"} if error.status_code == 429 else None
    return JSONResponse(
        status_code=error.status_code,
        content=error_payload(error.code, error.message, error.details),
        headers=headers,
    )


async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    details = {"fields": error.errors()}
    return JSONResponse(
        status_code=422,
        content=error_payload("VALIDATION_ERROR", "Validation failed", details),
    )
